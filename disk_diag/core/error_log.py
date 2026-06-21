"""Журнал ошибок диска: ATA SMART Error Log + NVMe Error Information Log.

Завершает «диагностическую глубину»: SMART (снимок) + trend (динамика) +
self-test (активная проверка) + **error log** (что уже ломалось). Read-only.

Транспорты (переиспользуют инфраструктуру self_test):
  ATA  (SATA/USB-SATA): SMART READ LOG (0xD5) @ log address 0x01 —
        Summary SMART Error Log (512 байт, до 5 последних ошибок).
  NVMe: Get Log Page LID 0x01 — Error Information Log (записи по 64 байта).

USB-NVMe мосты обычно не отдают error log через vendor-CDB — честный note.
"""

import ctypes
import logging
import struct

from .constants import (
    SMART_RCV_DRIVE_DATA, SMART_READ_LOG, SMART_LOG_ADDR_ERROR,
    NVME_LOG_PAGE_ERROR_INFO,
)
from .structures import SENDCMDOUTPARAMS
from .winapi import DeviceHandle, IoctlFailed, DiskAccessError
from .models import ErrorLogEntry, ErrorLog, InterfaceType
from .self_test import _ata_read_log, _nvme_get_log

logger = logging.getLogger(__name__)

# ATA error register (байт +1 error data structure) — биты дефектов
_ATA_ERROR_BITS = [
    (0x80, "ICRC (interface CRC)"),
    (0x40, "UNC (uncorrectable data)"),
    (0x20, "MC (media changed)"),
    (0x10, "IDNF (LBA out of range)"),
    (0x08, "MCR (media change request)"),
    (0x04, "ABRT (command aborted)"),
    (0x02, "NM (no media)"),
    (0x01, "AMNF (address mark not found)"),
]

# ATA «состояние диска» в момент ошибки (low nibble байта state)
_ATA_STATE = {
    0x0: "unknown", 0x1: "sleep", 0x2: "standby",
    0x3: "active/idle", 0x4: "offline/self-test",
}

# NVMe Status Code Type (bits 11:9 поля статуса)
_NVME_SCT = {
    0: "Generic", 1: "Command-Specific", 2: "Media/Data-Integrity",
    3: "Path-Related", 7: "Vendor-Specific",
}

# Сколько записей NVMe error log запрашивать (×64 байта)
_NVME_ERR_ENTRIES = 32


# ============================================================
#  ATA Summary Error Log (log 0x01)
# ============================================================

def _ata_read_error_log_raw(handle, use_sat):
    """Summary SMART Error Log (READ LOG 0xD5 @ 0x01) с fallback на ATA PT/SAT."""
    return _ata_read_log(handle, SMART_LOG_ADDR_ERROR, use_sat)


def _decode_ata_error_reg(reg: int) -> str:
    if reg == 0:
        return "Error (no flags)"
    flags = [name for bit, name in _ATA_ERROR_BITS if reg & bit]
    return ", ".join(flags) if flags else f"0x{reg:02X}"


def _parse_ata_error_log(data: bytes):
    """Распарсить Summary SMART Error Log (512 байт) → (entries, device_error_count).

    Раскладка: [0] version, [1] error log index, [2..451] 5 структур по 90 байт,
    [452..453] ATA device error count. Структура: [0..59] 5 команд по 12 байт,
    [60..89] error data structure (error register / status / LBA / state / POH).
    """
    entries = []
    if len(data) < 454:
        return entries, 0
    device_error_count = struct.unpack_from("<H", data, 452)[0]

    for i in range(5):
        err = 2 + i * 90 + 60  # начало error data structure
        error_reg = data[err + 1]
        status_reg = data[err + 7]
        lba = data[err + 3] | (data[err + 4] << 8) | (data[err + 5] << 16)
        state = data[err + 27] & 0x0F
        poh = struct.unpack_from("<H", data, err + 28)[0]
        # пустой слот — всё по нулям
        if error_reg == 0 and status_reg == 0 and poh == 0 and lba == 0:
            continue
        entries.append(ErrorLogEntry(
            number=i + 1,
            description=_decode_ata_error_reg(error_reg),
            lba=(lba if lba else -1),
            lifetime_hours=poh,
            detail=f"status=0x{status_reg:02X}, {_ATA_STATE.get(state, f'state {state}')}",
        ))

    # Журнал кольцевой; новейшие — с большей наработкой. Перенумеруем 1..N.
    entries.sort(key=lambda e: e.lifetime_hours, reverse=True)
    for n, e in enumerate(entries, 1):
        e.number = n
    return entries, device_error_count


# ============================================================
#  NVMe Error Information Log (LID 0x01)
# ============================================================

def _nvme_read_error_log_raw(handle):
    """Get Log Page 0x01 (Error Information Log).

    Пробуем _NVME_ERR_ENTRIES записей, при отказе — 1 запись: контроллеры с малым
    ELPE (макс. число записей) могут отвергнуть большой NUMD, и тогда падать с
    «не поддерживается» неправильно — минимум 1 запись поддерживается всегда.
    """
    last_err = None
    for count in (_NVME_ERR_ENTRIES, 1):
        try:
            return _nvme_get_log(handle, NVME_LOG_PAGE_ERROR_INFO, count * 64)
        except (IoctlFailed, DiskAccessError) as e:
            last_err = e
    raise last_err


def _parse_nvme_error_log(data: bytes):
    """Распарсить NVMe Error Information Log → list[ErrorLogEntry].

    Запись 64 байта: [0..7] Error Count (0 = не используется), [8..9] SQID,
    [10..11] CmdID, [12..13] Status Field, [16..23] LBA, [24..27] NSID.
    """
    entries = []
    n = len(data) // 64
    for i in range(n):
        off = i * 64
        error_count = struct.unpack_from("<Q", data, off)[0]
        if error_count == 0:
            continue  # запись не используется
        sqid = struct.unpack_from("<H", data, off + 8)[0]
        cmdid = struct.unpack_from("<H", data, off + 10)[0]
        status = struct.unpack_from("<H", data, off + 12)[0]
        lba = struct.unpack_from("<Q", data, off + 16)[0]
        nsid = struct.unpack_from("<I", data, off + 24)[0]
        # Status Field: bit0 = Phase Tag, bits 8:1 = SC, bits 11:9 = SCT
        sc = (status >> 1) & 0xFF
        sct = (status >> 9) & 0x7
        nsid_str = "all" if nsid == 0xFFFFFFFF else str(nsid)
        entries.append(ErrorLogEntry(
            number=error_count,
            description=f"{_NVME_SCT.get(sct, f'SCT {sct}')} / SC 0x{sc:02X}",
            lba=(lba if lba != 0xFFFFFFFFFFFFFFFF else -1),
            lifetime_hours=-1,  # запись NVMe error log не содержит наработки
            detail=f"NSID={nsid_str}, SQID={sqid}, CmdID=0x{cmdid:04X}",
        ))
    # Запись с наибольшим Error Count — самая свежая.
    entries.sort(key=lambda e: e.number, reverse=True)
    return entries


# ============================================================
#  Движок
# ============================================================

class ErrorLogEngine:
    """Чтение журнала ошибок с выбором транспорта по интерфейсу. Read-only."""

    def __init__(self, drive_number: int, interface_type: str):
        self.drive_number = drive_number
        self.interface = interface_type
        self._is_nvme = (interface_type == InterfaceType.NVME.value)
        self._use_sat = (interface_type == InterfaceType.USB.value)

    def read(self) -> ErrorLog:
        """Прочитать журнал. Не бросает — возвращает supported=False при отказе."""
        try:
            with DeviceHandle(self.drive_number, read_only=False) as h:
                if self._is_nvme:
                    entries = _parse_nvme_error_log(_nvme_read_error_log_raw(h))
                    return ErrorLog(entries=entries, total_count=len(entries))
                raw = _ata_read_error_log_raw(h, self._use_sat)
                entries, total = _parse_ata_error_log(raw)
                return ErrorLog(entries=entries, total_count=total)
        except (IoctlFailed, DiskAccessError) as e:
            logger.info(f"Error log unavailable on drive {self.drive_number}: {e}")
            return ErrorLog(supported=False, note=self._note(str(e)))

    def _note(self, err: str) -> str:
        if self._use_sat:
            return ("Журнал ошибок недоступен через этот USB-мост "
                    "(возможно, USB-NVMe). Подключите диск напрямую (SATA/M.2).")
        return f"Журнал ошибок не поддерживается этим диском или драйвером ({err})."
