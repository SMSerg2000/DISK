"""Чтение NVMe SMART / Health Info через Windows IOCTL + WMI fallback."""

import ctypes
import json
import logging
import struct
import subprocess

from .constants import (
    IOCTL_STORAGE_QUERY_PROPERTY,
    StorageDeviceProtocolSpecificProperty,
    StorageAdapterProtocolSpecificProperty,
    PropertyStandardQuery,
    ProtocolTypeNvme,
    NVMeDataTypeLogPage,
    NVME_LOG_PAGE_HEALTH_INFO,
)
from .winapi import DeviceHandle, IoctlFailed, DiskAccessError
from .models import NvmeHealthInfo

logger = logging.getLogger(__name__)

# Размер NVME_HEALTH_INFO_LOG
_HEALTH_LOG_SIZE = 512

# IOCTL_STORAGE_PROTOCOL_COMMAND
# CTL_CODE(FILE_DEVICE_MASS_STORAGE=0x2D, 0x04F0, METHOD_BUFFERED=0, FILE_READ_WRITE_ACCESS=3)
IOCTL_STORAGE_PROTOCOL_COMMAND = 0x002DD3C0


def _parse_128bit(data: bytes | list) -> int:
    """Преобразовать 16-байтное little-endian значение в Python int."""
    if isinstance(data, (list, tuple)):
        data = bytes(data)
    return int.from_bytes(data[:16], byteorder="little")


def _parse_raw_health(health_data: bytes) -> NvmeHealthInfo:
    """Распарсить сырые 512-байт NVMe Health Information Log Page."""
    if len(health_data) < 200:
        raise IoctlFailed("NVMe Health Parse", 0, "Response too short")

    critical_warning = health_data[0]

    # Temperature: 2 bytes little-endian, в Кельвинах
    temp_kelvin = struct.unpack_from("<H", health_data, 1)[0]
    temp_celsius = temp_kelvin - 273

    available_spare = health_data[3]
    available_spare_threshold = health_data[4]
    percentage_used = health_data[5]

    # 128-bit поля начинаются с offset 32
    offset = 32
    data_units_read = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    data_units_written = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    host_read_commands = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    host_write_commands = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    controller_busy_time = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    power_cycles = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    power_on_hours = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    unsafe_shutdowns = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    media_errors = _parse_128bit(health_data[offset:offset + 16]); offset += 16
    error_log_entries = _parse_128bit(health_data[offset:offset + 16]); offset += 16

    # Warning/Critical temperature time (4 bytes each)
    warning_temp_time = struct.unpack_from("<I", health_data, offset)[0]; offset += 4
    critical_temp_time = struct.unpack_from("<I", health_data, offset)[0]; offset += 4

    # Temperature sensors (8 x uint16, в Кельвинах)
    temperature_sensors = []
    for i in range(8):
        if offset + 2 <= len(health_data):
            sensor_k = struct.unpack_from("<H", health_data, offset)[0]
            if sensor_k > 0:
                temperature_sensors.append(sensor_k - 273)
            offset += 2

    return NvmeHealthInfo(
        critical_warning=critical_warning,
        temperature_celsius=temp_celsius,
        available_spare=available_spare,
        available_spare_threshold=available_spare_threshold,
        percentage_used=percentage_used,
        data_units_read=data_units_read,
        data_units_written=data_units_written,
        host_read_commands=host_read_commands,
        host_write_commands=host_write_commands,
        controller_busy_time=controller_busy_time,
        power_cycles=power_cycles,
        power_on_hours=power_on_hours,
        unsafe_shutdowns=unsafe_shutdowns,
        media_errors=media_errors,
        error_log_entries=error_log_entries,
        warning_temp_time=warning_temp_time,
        critical_temp_time=critical_temp_time,
        temperature_sensors=temperature_sensors,
    )


# ============================================================
# Method 1: IOCTL_STORAGE_QUERY_PROPERTY (protocol-specific)
# Реализация через ctypes.Structure + sizeof (без магических чисел)
# ============================================================

# STORAGE_PROTOCOL_SPECIFIC_DATA — разные версии Windows SDK
# определяют разное количество полей. Пробуем все варианты.

class _ProtoData7(ctypes.Structure):
    """7 fields, 28 bytes (Windows 10 1607)."""
    _fields_ = [
        ("ProtocolType", ctypes.c_ulong),
        ("DataType", ctypes.c_ulong),
        ("ProtocolDataRequestValue", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue", ctypes.c_ulong),
        ("ProtocolDataOffset", ctypes.c_ulong),
        ("ProtocolDataLength", ctypes.c_ulong),
        ("FixedProtocolReturnData", ctypes.c_ulong),
    ]

class _ProtoData10(ctypes.Structure):
    """10 fields, 40 bytes (Windows 10 1809+ / Windows 11)."""
    _fields_ = [
        ("ProtocolType", ctypes.c_ulong),
        ("DataType", ctypes.c_ulong),
        ("ProtocolDataRequestValue", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue", ctypes.c_ulong),
        ("ProtocolDataOffset", ctypes.c_ulong),
        ("ProtocolDataLength", ctypes.c_ulong),
        ("FixedProtocolReturnData", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue2", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue3", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue4", ctypes.c_ulong),
    ]

class _ProtoData11(ctypes.Structure):
    """11 fields, 44 bytes (latest Windows 11 SDK)."""
    _fields_ = [
        ("ProtocolType", ctypes.c_ulong),
        ("DataType", ctypes.c_ulong),
        ("ProtocolDataRequestValue", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue", ctypes.c_ulong),
        ("ProtocolDataOffset", ctypes.c_ulong),
        ("ProtocolDataLength", ctypes.c_ulong),
        ("FixedProtocolReturnData", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue2", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue3", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue4", ctypes.c_ulong),
        ("ProtocolDataRequestSubValue5", ctypes.c_ulong),
    ]

# Порядок: 40B (самый вероятный для Win11), 44B, 28B (legacy)
_PROTO_VARIANTS = [
    (_ProtoData10, "40B"),
    (_ProtoData11, "44B"),
    (_ProtoData7, "28B"),
]

# PropertyId(4) + QueryType(4) = фиксированная часть STORAGE_PROPERTY_QUERY
_QUERY_HEADER_SIZE = 8


def _try_query_property_v2(
    handle: DeviceHandle,
    prop_id: int,
    proto_class: type,
    label: str,
) -> NvmeHealthInfo:
    """IOCTL_STORAGE_QUERY_PROPERTY — через ctypes.sizeof, без магических чисел.

    Единый буфер (in/out). После вызова валидируем descriptor.

    Input layout:
      [0..7]     PropertyId + QueryType
      [8..8+P-1] STORAGE_PROTOCOL_SPECIFIC_DATA (P = sizeof)
      [8+P..]    зарезервировано под health data (512 байт)

    Output layout (STORAGE_PROTOCOL_DATA_DESCRIPTOR + data):
      [0..7]     Version + Size
      [8..8+P-1] ProtocolSpecificData (заполняет драйвер)
      [8+off..]  Health data, off = ProtocolDataOffset из ответа
    """
    proto_size = ctypes.sizeof(proto_class)
    buf_size = _QUERY_HEADER_SIZE + proto_size + _HEALTH_LOG_SIZE

    logger.debug(
        f"NVMe QP/{label}: sizeof(proto)={proto_size}, "
        f"buf_size={buf_size}, prop_id={prop_id}"
    )

    # Собираем буфер через ctypes
    buf = (ctypes.c_ubyte * buf_size)()
    ctypes.memset(buf, 0, buf_size)

    # STORAGE_PROPERTY_QUERY: PropertyId + QueryType
    ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))[0] = prop_id
    ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))[1] = PropertyStandardQuery

    # STORAGE_PROTOCOL_SPECIFIC_DATA — с offset 8 (через from_buffer)
    proto = proto_class.from_buffer(buf, _QUERY_HEADER_SIZE)
    proto.ProtocolType = ProtocolTypeNvme
    proto.DataType = NVMeDataTypeLogPage
    proto.ProtocolDataRequestValue = NVME_LOG_PAGE_HEALTH_INFO
    proto.ProtocolDataRequestSubValue = 0
    proto.ProtocolDataOffset = proto_size
    proto.ProtocolDataLength = _HEALTH_LOG_SIZE

    # DeviceIoControl (единый буфер in/out)
    buf_ba = bytearray(bytes(buf))
    returned = handle.ioctl_inplace(IOCTL_STORAGE_QUERY_PROPERTY, buf_ba)

    # --- Разбор ответа (STORAGE_PROTOCOL_DATA_DESCRIPTOR) ---
    resp_version = struct.unpack_from("<I", buf_ba, 0)[0]
    resp_size = struct.unpack_from("<I", buf_ba, 4)[0]

    # ProtocolDataOffset/Length — 5-й и 6-й DWORD внутри proto structure
    resp_data_offset = struct.unpack_from("<I", buf_ba, _QUERY_HEADER_SIZE + 16)[0]
    resp_data_length = struct.unpack_from("<I", buf_ba, _QUERY_HEADER_SIZE + 20)[0]

    logger.debug(
        f"NVMe QP/{label} response: returned={returned}, "
        f"ver={resp_version}, size={resp_size}, "
        f"data_off={resp_data_offset}, data_len={resp_data_length}"
    )

    if resp_data_length == 0 or resp_data_length > _HEALTH_LOG_SIZE + 64:
        raise IoctlFailed(
            f"QueryProperty/{label}", 0,
            f"Bad ProtocolDataLength={resp_data_length}",
        )

    data_start = _QUERY_HEADER_SIZE + resp_data_offset
    data_end = data_start + resp_data_length
    if data_end > len(buf_ba):
        raise IoctlFailed(
            f"QueryProperty/{label}", 0,
            f"Data overflows: start={data_start}, len={resp_data_length}, buf={len(buf_ba)}",
        )

    health_bytes = buf_ba[data_start:data_end]

    if all(b == 0 for b in health_bytes[:32]):
        raise IoctlFailed(f"QueryProperty/{label}", 0, "Health data is all zeros")

    logger.info(f"NVMe health OK via QueryProperty/{label}")
    return _parse_raw_health(health_bytes)


# ============================================================
# Method 2: IOCTL_STORAGE_PROTOCOL_COMMAND (raw NVMe Admin cmd)
# ============================================================

def _try_protocol_command(handle: DeviceHandle, flags: int, label: str) -> NvmeHealthInfo:
    """Прямая NVMe Admin команда Get Log Page через IOCTL_STORAGE_PROTOCOL_COMMAND.

    STORAGE_PROTOCOL_COMMAND layout:
      [0..79]   — заголовок (20 DWORDs)
      [80..143] — NVMe Submission Queue Entry (64 bytes)
      [144..207]— Error Info buffer (64 bytes)
      [208..719]— Data from device (512 bytes)
    """
    cmd_len = 64       # NVMe SQE
    err_info_len = 64  # NVMe Error Info Log Entry
    data_size = _HEALTH_LOG_SIZE  # 512
    header_size = 80 + cmd_len  # 144

    err_info_offset = header_size  # 144
    data_offset = err_info_offset + err_info_len  # 208
    buf_size = data_offset + data_size  # 720

    buf = bytearray(buf_size)

    # STORAGE_PROTOCOL_COMMAND header
    struct.pack_into("<I", buf, 0, 1)                # Version = STORAGE_PROTOCOL_STRUCTURE_VERSION
    struct.pack_into("<I", buf, 4, header_size)      # Length
    struct.pack_into("<I", buf, 8, ProtocolTypeNvme) # ProtocolType = 3 (NVMe)
    struct.pack_into("<I", buf, 12, flags)           # Flags
    # [16] ReturnStatus — output
    # [20] ErrorCode — output
    struct.pack_into("<I", buf, 24, cmd_len)         # CommandLength = 64
    struct.pack_into("<I", buf, 28, err_info_len)    # ErrorInfoLength = 64
    struct.pack_into("<I", buf, 32, 0)               # DataToDeviceTransferLength = 0
    struct.pack_into("<I", buf, 36, data_size)       # DataFromDeviceTransferLength = 512
    struct.pack_into("<I", buf, 40, 10)              # TimeOutValue = 10 sec
    struct.pack_into("<I", buf, 44, err_info_offset) # ErrorInfoOffset = 144
    struct.pack_into("<I", buf, 48, 0)               # DataToDeviceBufferOffset = 0
    struct.pack_into("<I", buf, 52, data_offset)     # DataFromDeviceBufferOffset = 208
    struct.pack_into("<I", buf, 56, 1)               # CommandSpecific = GET_LOG_PAGE_DATA

    # NVMe Admin Command: Get Log Page (opcode 0x02)
    cmd = 80  # offset в buf
    struct.pack_into("<I", buf, cmd + 0, 0x02)          # CDW0: Opcode = Get Log Page
    struct.pack_into("<I", buf, cmd + 4, 0xFFFFFFFF)    # NSID = broadcast
    # CDW10: (NUMDL << 16) | LID
    numdl = (data_size // 4) - 1  # 127
    cdw10 = (numdl << 16) | NVME_LOG_PAGE_HEALTH_INFO
    struct.pack_into("<I", buf, cmd + 40, cdw10)        # CDW10

    # Отправляем — единый буфер (METHOD_BUFFERED)
    returned = handle.ioctl_inplace(IOCTL_STORAGE_PROTOCOL_COMMAND, buf)

    # Проверяем ReturnStatus
    ret_status = struct.unpack_from("<I", buf, 16)[0]
    err_code = struct.unpack_from("<I", buf, 20)[0]
    if ret_status != 0:
        raise IoctlFailed("NVMe ProtocolCommand", ret_status,
                          f"ReturnStatus=0x{ret_status:X}, ErrorCode=0x{err_code:X}")

    logger.info(f"NVMe health OK via ProtocolCommand/{label}")
    return _parse_raw_health(buf[data_offset:data_offset + data_size])


# ============================================================
# Method 3: IOCTL_SCSI_MINIPORT + NvmeMini signature
# ============================================================

# CTL_CODE(FILE_DEVICE_CONTROLLER=0x04, 0x0402, METHOD_BUFFERED=0, FILE_READ_WRITE_ACCESS=3)
IOCTL_SCSI_MINIPORT = 0x0004D008

# CTL_CODE(FILE_DEVICE_CONTROLLER=0x04, 0x0406, METHOD_BUFFERED=0, FILE_ANY_ACCESS=0)
IOCTL_SCSI_GET_ADDRESS = 0x00041018

# ControlCode для NVMe passthrough через SRB_IO_CONTROL
NVME_PASS_THROUGH_SRB_IO_CODE = 0xE0002000


def _get_scsi_port(handle: DeviceHandle) -> int:
    """Получить номер SCSI порта для диска (для открытия адаптера)."""
    # SCSI_ADDRESS: Length(4) + PortNumber(1) + PathId(1) + TargetId(1) + Lun(1) = 8 bytes
    query = bytearray(8)
    struct.pack_into("<I", query, 0, 8)  # Length = sizeof(SCSI_ADDRESS)
    result = handle.ioctl_raw(IOCTL_SCSI_GET_ADDRESS, bytes(query), 8)
    if len(result) >= 5:
        return result[4]  # PortNumber at offset 4
    raise IoctlFailed("SCSI_GET_ADDRESS", 0, f"Response too short: {len(result)}")


def _build_miniport_nvme_buf(data_size: int = _HEALTH_LOG_SIZE) -> bytearray:
    """Построить буфер IOCTL_SCSI_MINIPORT с NVMe Get Log Page командой.

    Layout:
      [0..27]    SRB_IO_CONTROL (28 bytes)
      [28..51]   VendorSpecific[6] (24 bytes)
      [52..115]  NVMeCmd[16] — NVMe SQE (64 bytes)
      [116..131] CplEntry[4] — NVMe CQE output (16 bytes)
      [132]      Direction (ULONG)
      [136]      QueueId (ULONG)
      [140]      DataBufferLen (ULONG)
      [144]      MetaDataLen (ULONG)
      [148]      ReturnBufferLen (ULONG)
      [152..]    DataBuffer (data_size bytes)
    """
    header_size = 152  # fixed part before DataBuffer
    buf_size = header_size + data_size
    buf = bytearray(buf_size)

    # SRB_IO_CONTROL
    struct.pack_into("<I", buf, 0, 28)            # HeaderLength = sizeof(SRB_IO_CONTROL)
    buf[4:12] = b"NvmeMini"                       # Signature
    struct.pack_into("<I", buf, 12, 10)           # Timeout = 10 sec
    struct.pack_into("<I", buf, 16, NVME_PASS_THROUGH_SRB_IO_CODE)  # ControlCode
    # [20] ReturnCode — output
    struct.pack_into("<I", buf, 24, buf_size - 28)  # Length = data after SRB_IO_CONTROL

    # NVME_PASS_THROUGH_IOCTL fields
    # VendorSpecific[6] at offset 28 — zeros
    # NVMeCmd[16] at offset 52 — NVMe Get Log Page command
    cmd = 52
    struct.pack_into("<I", buf, cmd + 0, 0x02)          # CDW0: Opcode = Get Log Page
    struct.pack_into("<I", buf, cmd + 4, 0xFFFFFFFF)    # NSID = broadcast
    numdl = (data_size // 4) - 1  # 127
    cdw10 = (numdl << 16) | NVME_LOG_PAGE_HEALTH_INFO
    struct.pack_into("<I", buf, cmd + 40, cdw10)        # CDW10

    # CplEntry[4] at offset 116 — zeros, filled by driver
    struct.pack_into("<I", buf, 132, 2)               # Direction = 2 (read from device)
    struct.pack_into("<I", buf, 136, 0)               # QueueId = 0 (admin)
    struct.pack_into("<I", buf, 140, data_size)       # DataBufferLen = 512
    struct.pack_into("<I", buf, 144, 0)               # MetaDataLen = 0
    struct.pack_into("<I", buf, 148, buf_size)        # ReturnBufferLen = total

    return buf


def _try_scsi_miniport_nvme(drive_number: int) -> NvmeHealthInfo:
    """NVMe health через IOCTL_SCSI_MINIPORT с сигнатурой NvmeMini.

    Работает через SCSI adapter device (\\\\.\\ScsiN:), а не через PhysicalDriveN.
    """
    # Шаг 1: получить номер SCSI порта
    with DeviceHandle(drive_number, read_only=True) as h:
        port = _get_scsi_port(h)
    logger.debug(f"NVMe disk on SCSI port {port}")

    # Шаг 2: открыть адаптер и послать IOCTL_SCSI_MINIPORT
    adapter_path = f"\\\\.\\Scsi{port}:"
    buf = _build_miniport_nvme_buf()

    with DeviceHandle(device_path=adapter_path, read_only=False) as h:
        returned = h.ioctl_inplace(IOCTL_SCSI_MINIPORT, buf)

    # Проверяем ReturnCode в SRB_IO_CONTROL
    ret_code = struct.unpack_from("<I", buf, 20)[0]
    if ret_code != 0:
        raise IoctlFailed("SCSI_MINIPORT/NvmeMini", ret_code,
                          f"SRB ReturnCode=0x{ret_code:X}")

    logger.info(f"NVMe health OK via SCSI_MINIPORT/NvmeMini (adapter={adapter_path})")
    return _parse_raw_health(buf[152:152 + _HEALTH_LOG_SIZE])


# ============================================================
# Method 4: PowerShell / WMI fallback
# ============================================================

def _read_nvme_health_wmi(drive_number: int) -> NvmeHealthInfo:
    """Fallback: чтение NVMe health через PowerShell Get-StorageReliabilityCounter."""
    ps_script = (
        f"$d = Get-PhysicalDisk | Where-Object DeviceId -eq '{drive_number}';"
        f"if (-not $d) {{ Write-Error 'Disk not found'; exit 1 }};"
        f"$r = $d | Get-StorageReliabilityCounter;"
        f"if (-not $r) {{ Write-Error 'No reliability data'; exit 1 }};"
        f"$r | Select-Object Temperature, Wear, PowerOnHours, StartStopCycleCount,"
        f"  ReadErrorsUncorrected, ReadErrorsTotal, WriteErrorsTotal,"
        f"  ReadLatencyMax, WriteLatencyMax, FlushLatencyMax"
        f" | ConvertTo-Json"
    )

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise DiskAccessError(f"PowerShell fallback failed: {e}")

    if result.returncode != 0:
        stderr = result.stderr.strip()[:200]
        raise DiskAccessError(f"PowerShell error: {stderr}")

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as e:
        raise DiskAccessError(f"PowerShell JSON parse error: {e}")

    logger.info(f"NVMe health via PowerShell/WMI: {data}")

    return NvmeHealthInfo(
        critical_warning=0,
        temperature_celsius=int(data.get("Temperature") or 0),
        available_spare=0,
        available_spare_threshold=0,
        percentage_used=int(data.get("Wear") or 0),
        data_units_read=0,
        data_units_written=0,
        host_read_commands=0,
        host_write_commands=0,
        controller_busy_time=0,
        power_cycles=int(data.get("StartStopCycleCount") or 0),
        power_on_hours=int(data.get("PowerOnHours") or 0),
        unsafe_shutdowns=0,
        media_errors=int(data.get("ReadErrorsUncorrected") or 0),
        error_log_entries=(
            int(data.get("ReadErrorsTotal") or 0)
            + int(data.get("WriteErrorsTotal") or 0)
        ),
        warning_temp_time=0,
        critical_temp_time=0,
        temperature_sensors=[],
        wmi_fallback=True,
    )


# ============================================================
# Auto-detection: try all methods
# ============================================================

def read_nvme_health_auto(drive_number: int) -> NvmeHealthInfo:
    """Автоматический выбор метода чтения NVMe health.

    Порядок:
    1. IOCTL_STORAGE_QUERY_PROPERTY на PhysicalDrive (3 размера proto × 2 PropertyId × RW/RO)
    2. IOCTL_STORAGE_QUERY_PROPERTY через Scsi adapter (3 × 2)
    3. IOCTL_STORAGE_PROTOCOL_COMMAND (прямая NVMe Admin команда)
    4. IOCTL_SCSI_MINIPORT + NvmeMini
    5. PowerShell/WMI fallback
    """
    errors = []

    property_ids = [
        (StorageDeviceProtocolSpecificProperty, "Dev"),
        (StorageAdapterProtocolSpecificProperty, "Adp"),
    ]

    # Method 1: QueryProperty на PhysicalDrive (RW first — NVMe обычно требует RW)
    for read_only in [False, True]:
        mode = "RO" if read_only else "RW"
        try:
            with DeviceHandle(drive_number, read_only=read_only) as h:
                for prop_id, prop_label in property_ids:
                    for proto_class, size_label in _PROTO_VARIANTS:
                        label = f"{prop_label}/{size_label}/{mode}"
                        try:
                            return _try_query_property_v2(
                                h, prop_id, proto_class, label,
                            )
                        except (IoctlFailed, DiskAccessError) as e:
                            logger.debug(f"NVMe QP/{label}: {e}")
                            errors.append(f"QP/{label}: {e}")
        except DiskAccessError as e:
            errors.append(f"Handle({mode}): {e}")

    # Method 2: QueryProperty через Scsi adapter device
    try:
        with DeviceHandle(drive_number, read_only=True) as h:
            port = _get_scsi_port(h)
        adapter_path = f"\\\\.\\Scsi{port}:"
        logger.debug(f"Trying NVMe QP via adapter {adapter_path}")
        with DeviceHandle(device_path=adapter_path, read_only=False) as ah:
            for prop_id, prop_label in property_ids:
                for proto_class, size_label in _PROTO_VARIANTS:
                    label = f"Scsi/{prop_label}/{size_label}"
                    try:
                        return _try_query_property_v2(
                            ah, prop_id, proto_class, label,
                        )
                    except (IoctlFailed, DiskAccessError) as e:
                        logger.debug(f"NVMe QP/{label}: {e}")
                        errors.append(f"QP/{label}: {e}")
    except Exception as e:
        logger.debug(f"NVMe adapter QP failed: {e}")
        errors.append(f"AdapterQP: {e}")

    # Method 3: IOCTL_STORAGE_PROTOCOL_COMMAND (requires RW + registry key)
    protocol_cmd_flags = [
        (0x80000000, "AdapterRequest"),
        (0, "DeviceRequest"),
    ]
    try:
        with DeviceHandle(drive_number, read_only=False) as h:
            for flags, flag_label in protocol_cmd_flags:
                try:
                    return _try_protocol_command(h, flags, flag_label)
                except (IoctlFailed, DiskAccessError) as e:
                    logger.debug(f"NVMe ProtocolCmd/{flag_label}: {e}")
                    errors.append(f"ProtocolCmd/{flag_label}: {e}")
    except DiskAccessError as e:
        errors.append(f"Handle(RW): {e}")

    # Method 4: IOCTL_SCSI_MINIPORT + NvmeMini
    try:
        return _try_scsi_miniport_nvme(drive_number)
    except Exception as e:
        logger.debug(f"NVMe SCSI_MINIPORT: {e}")
        errors.append(f"SCSI_MINIPORT: {e}")

    # Method 5: PowerShell/WMI fallback
    logger.info("All NVMe IOCTLs failed, trying PowerShell/WMI fallback...")
    try:
        health = _read_nvme_health_wmi(drive_number)
        logger.info("NVMe health via PowerShell/WMI OK")
        return health
    except Exception as e:
        errors.append(f"WMI: {e}")

    raise DiskAccessError(
        "NVMe health: all methods failed:\n" + "\n".join(errors)
    )
