"""USB-NVMe bridge SMART pass-through (JMicron, ASMedia, Realtek).

USB-NVMe мосты не поддерживают стандартные NVMe IOCTL через Windows
storage stack. Для чтения SMART используются vendor-specific SCSI
команды, туннелирующие NVMe Admin commands через USB.

Поддерживаемые мосты:
- JMicron JMS583/JMS581 (CDB 0xA1, 3-шаговый протокол)
- ASMedia ASM2362/ASM2364 (CDB 0xE6, 1-шаговый)
- Realtek RTL9210/RTL9211/RTL9220 (CDB 0xE4, 1-шаговый)
"""

import ctypes
import struct
import logging
from typing import Optional

from .winapi import DeviceHandle, IoctlFailed
from .structures import SCSI_PASS_THROUGH
from .constants import IOCTL_SCSI_PASS_THROUGH, SCSI_IOCTL_DATA_IN
from .smart_nvme import _parse_raw_health
from .models import NvmeHealthInfo

logger = logging.getLogger(__name__)

# SCSI data directions
_DATA_OUT = 0
_DATA_IN = SCSI_IOCTL_DATA_IN  # 1

# NVMe constants
_NVME_SIG = 0x454D564E       # "NVME" little-endian
_NVME_GET_LOG = 0x02          # Get Log Page opcode
_NVME_LOG_SMART = 0x02        # SMART / Health Information Log ID
_SMART_SIZE = 512             # NVMe SMART log size


# ────────────────────────────────────────────────────────────
#  Low-level SCSI pass-through helper
# ────────────────────────────────────────────────────────────

def _scsi_cmd(handle: DeviceHandle, cdb: bytes | bytearray,
              direction: int, data_size: int,
              send_data: bytes | bytearray | None = None,
              timeout: int = 10) -> bytes:
    """Execute SCSI command via IOCTL_SCSI_PASS_THROUGH (buffered).

    Формат буфера: [SCSI_PASS_THROUGH | sense(32) | data(N)]
    Для DATA_OUT send_data копируется в data-область перед отправкой.
    Для DATA_IN данные читаются из data-области ответа.
    """
    header_size = ctypes.sizeof(SCSI_PASS_THROUGH)
    sense_size = 32
    sense_offset = header_size
    data_offset = (sense_offset + sense_size + 7) & ~7  # align to 8
    total_size = data_offset + data_size

    buf = bytearray(total_size)

    # ── SCSI_PASS_THROUGH header (используем .offset для корректности) ──
    struct.pack_into("<H", buf, 0, header_size)              # Length
    buf[6] = len(cdb)                                        # CdbLength
    buf[7] = sense_size                                      # SenseInfoLength
    buf[8] = direction                                       # DataIn

    dtl_off = SCSI_PASS_THROUGH.DataTransferLength.offset
    struct.pack_into("<I", buf, dtl_off, data_size)

    tov_off = SCSI_PASS_THROUGH.TimeOutValue.offset
    struct.pack_into("<I", buf, tov_off, timeout)

    dbo_off = SCSI_PASS_THROUGH.DataBufferOffset.offset
    ptr_size = ctypes.sizeof(ctypes.c_size_t)
    if ptr_size == 8:
        struct.pack_into("<Q", buf, dbo_off, data_offset)
    else:
        struct.pack_into("<I", buf, dbo_off, data_offset)

    sio_off = SCSI_PASS_THROUGH.SenseInfoOffset.offset
    struct.pack_into("<I", buf, sio_off, sense_offset)

    # CDB
    cdb_off = SCSI_PASS_THROUGH.Cdb.offset
    for i, b in enumerate(cdb):
        buf[cdb_off + i] = b

    # DATA_OUT: вложить данные для отправки
    if direction == _DATA_OUT and send_data:
        for i, b in enumerate(send_data[:data_size]):
            buf[data_offset + i] = b

    result = handle.ioctl_raw(IOCTL_SCSI_PASS_THROUGH, bytes(buf), total_size)

    # DATA_OUT: ответ — только заголовок (без данных), это нормально
    if direction == _DATA_OUT:
        return b""

    if len(result) < data_offset + data_size:
        raise IoctlFailed("SCSI_PASS_THROUGH", 0,
                          f"Response too short: {len(result)} < {data_offset + data_size}")

    return result[data_offset:data_offset + data_size]


# ────────────────────────────────────────────────────────────
#  JMicron Protocol (JMS583, JMS581, JMS586)
#  CDB opcode 0xA1 (ATA_PASSTHROUGH_12, перехватывается мостом)
#  3-шаговый: send NVM cmd → DMA-IN data → (get completion)
# ────────────────────────────────────────────────────────────

def _jmicron_get_smart(handle: DeviceHandle) -> bytes:
    """Read NVMe SMART via JMicron USB bridge."""

    # ── Step 1: Send NVMe command payload (DATA_OUT, 512 bytes) ──
    nvme_cmd = bytearray(512)
    struct.pack_into("<I", nvme_cmd, 0x00, _NVME_SIG)       # Signature "NVME"
    struct.pack_into("<I", nvme_cmd, 0x08, _NVME_GET_LOG)    # NVMe Opcode
    struct.pack_into("<I", nvme_cmd, 0x0C, 0xFFFFFFFF)       # NSID (broadcast)
    # CDW10 = LID | (NUMDL << 16)
    cdw10 = _NVME_LOG_SMART | ((_SMART_SIZE // 4 - 1) << 16)
    struct.pack_into("<I", nvme_cmd, 0x30, cdw10)            # CDW10

    cdb1 = bytearray(12)
    cdb1[0] = 0xA1   # ATA_PASSTHROUGH_12 (intercepted by JMicron)
    cdb1[1] = 0x80   # admin | nvm_cmd (protocol = 0)
    cdb1[3] = 0x00   # transfer length BE24 high
    cdb1[4] = 0x02   # transfer length BE24 mid  (512 = 0x000200)
    cdb1[5] = 0x00   # transfer length BE24 low

    _scsi_cmd(handle, cdb1, _DATA_OUT, 512, send_data=nvme_cmd)

    # ── Step 2: DMA-IN — receive SMART data (DATA_IN, 512 bytes) ──
    cdb2 = bytearray(12)
    cdb2[0] = 0xA1
    cdb2[1] = 0x82   # admin | dma_in (protocol = 2)
    cdb2[3] = 0x00
    cdb2[4] = 0x02   # 512 bytes
    cdb2[5] = 0x00

    return _scsi_cmd(handle, cdb2, _DATA_IN, _SMART_SIZE)


# ────────────────────────────────────────────────────────────
#  ASMedia Protocol (ASM2362, ASM2364)
#  CDB opcode 0xE6, 1-шаговый
# ────────────────────────────────────────────────────────────

def _asmedia_get_smart(handle: DeviceHandle) -> bytes:
    """Read NVMe SMART via ASMedia USB bridge."""
    cdb = bytearray(16)
    cdb[0] = 0xE6                # ASMedia vendor opcode
    cdb[1] = _NVME_GET_LOG       # NVMe opcode
    cdb[3] = _NVME_LOG_SMART     # CDW10 byte0 = LID
    cdb[7] = 0x7F                # CDW10 byte[3:2] = NUMDL (127)

    return _scsi_cmd(handle, cdb, _DATA_IN, _SMART_SIZE)


# ────────────────────────────────────────────────────────────
#  Realtek Protocol (RTL9210, RTL9211, RTL9220)
#  CDB opcode 0xE4, 1-шаговый
# ────────────────────────────────────────────────────────────

def _realtek_get_smart(handle: DeviceHandle) -> bytes:
    """Read NVMe SMART via Realtek USB bridge."""
    cdb = bytearray(16)
    cdb[0] = 0xE4                # Realtek vendor opcode
    cdb[1] = _SMART_SIZE & 0xFF  # data size LE16 low  (512 = 0x0200)
    cdb[2] = (_SMART_SIZE >> 8) & 0xFF  # data size LE16 high
    cdb[3] = _NVME_GET_LOG       # NVMe opcode
    cdb[4] = _NVME_LOG_SMART     # CDW10 byte0 = LID

    return _scsi_cmd(handle, cdb, _DATA_IN, _SMART_SIZE)


# ────────────────────────────────────────────────────────────
#  Public API
# ────────────────────────────────────────────────────────────

_BRIDGE_METHODS = [
    ("JMicron", _jmicron_get_smart),
    ("ASMedia", _asmedia_get_smart),
    ("Realtek", _realtek_get_smart),
]


def read_usb_nvme_smart(drive_number: int) -> Optional[NvmeHealthInfo]:
    """Try to read NVMe SMART through USB-NVMe bridge.

    Пробует протоколы JMicron → ASMedia → Realtek.
    Возвращает NvmeHealthInfo или None если все провалились.
    """
    for name, method in _BRIDGE_METHODS:
        try:
            with DeviceHandle(drive_number, read_only=False) as h:
                raw = method(h)

            if raw and len(raw) >= 512 and any(raw[:32]):
                health = _parse_raw_health(bytes(raw))
                logger.info(f"USB-NVMe SMART OK via {name} bridge")
                return health

            logger.debug(f"USB-NVMe {name}: got data but looks empty")

        except Exception as e:
            logger.debug(f"USB-NVMe {name} failed: {e}")
            continue

    logger.info("USB-NVMe: all bridge protocols failed")
    return None
