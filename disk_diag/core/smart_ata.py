"""Чтение ATA SMART атрибутов и порогов через Windows IOCTL."""

import ctypes
import logging
import struct

from .constants import (
    SMART_RCV_DRIVE_DATA,
    SMART_SEND_DRIVE_COMMAND,
    ATA_SMART_CMD,
    SMART_READ_ATTRIBUTES,
    SMART_READ_THRESHOLDS,
    SMART_ENABLE_OPERATIONS,
    SMART_CYL_LOW, SMART_CYL_HI,
    IOCTL_ATA_PASS_THROUGH,
    ATA_FLAGS_DRDY_REQUIRED, ATA_FLAGS_DATA_IN,
    IOCTL_SCSI_PASS_THROUGH, SCSI_IOCTL_DATA_IN,
)
from .structures import (
    SENDCMDINPARAMS, SENDCMDOUTPARAMS, IDEREGS,
    ATA_PASS_THROUGH_EX, SCSI_PASS_THROUGH,
)
from .winapi import DeviceHandle, IoctlFailed
from .models import SmartAttribute, HealthLevel, DriveType
from ..data.smart_db import get_attribute_name, is_critical_attribute, SSD_INDICATOR_ATTRS

logger = logging.getLogger(__name__)

# Размер одной записи атрибута в SMART data
_ATTR_RECORD_SIZE = 12
# Максимум атрибутов в одном блоке
_MAX_ATTRIBUTES = 30
# Смещение данных атрибутов от начала 512-байтного буфера
_ATTR_DATA_OFFSET = 2


def _build_smart_command(feature: int, buffer_size: int = 512) -> SENDCMDINPARAMS:
    """Построить SENDCMDINPARAMS для SMART-команды.

    bDriveNumber всегда 0: для SATA каждый диск открывается через свой
    PhysicalDriveN handle, выбор устройства — по handle, а не по номеру.
    Поле bDriveNumber — легаси от IDE master/slave, на SATA игнорируется.
    """
    params = SENDCMDINPARAMS()
    params.cBufferSize = buffer_size
    params.bDriveNumber = 0  # Всегда 0 — устройство выбрано через handle

    params.irDriveRegs.bFeaturesReg = feature
    params.irDriveRegs.bSectorCountReg = 1
    params.irDriveRegs.bSectorNumberReg = 1
    params.irDriveRegs.bCylLowReg = SMART_CYL_LOW
    params.irDriveRegs.bCylHighReg = SMART_CYL_HI
    params.irDriveRegs.bDriveHeadReg = 0xA0  # Master (единственный вариант для SATA)
    params.irDriveRegs.bCommandReg = ATA_SMART_CMD

    return params


def _enable_smart(handle: DeviceHandle):
    """Отправить SMART ENABLE OPERATIONS перед чтением данных.

    Некоторые диски (WD, Seagate) требуют явного включения SMART.
    Если SMART уже включён — команда просто игнорируется.
    """
    cmd = _build_smart_command(SMART_ENABLE_OPERATIONS, buffer_size=0)
    try:
        handle.ioctl(SMART_SEND_DRIVE_COMMAND, cmd, ctypes.sizeof(SENDCMDOUTPARAMS))
        logger.debug("SMART ENABLE sent successfully")
    except IoctlFailed as e:
        logger.debug(f"SMART ENABLE failed (may already be enabled): {e}")


def _parse_raw_attributes(data: bytes) -> list[dict]:
    """Распарсить 512-байтный буфер SMART-атрибутов.

    Формат каждого атрибута (12 байт):
        [0]     Attribute ID (0 = пустой слот)
        [1-2]   Flags (little-endian)
        [3]     Current value (normalized, 1-253)
        [4]     Worst value
        [5-10]  Raw value (6 bytes, little-endian)
        [11]    Reserved
    """
    attributes = []
    offset = _ATTR_DATA_OFFSET

    for i in range(_MAX_ATTRIBUTES):
        if offset + _ATTR_RECORD_SIZE > len(data):
            break

        attr_id = data[offset]
        if attr_id == 0:
            offset += _ATTR_RECORD_SIZE
            continue

        flags = struct.unpack_from("<H", data, offset + 1)[0]
        current = data[offset + 3]
        worst = data[offset + 4]
        # Raw value: 6 bytes little-endian (берём как 48-bit int)
        raw_bytes = data[offset + 5:offset + 11]
        raw_value = int.from_bytes(raw_bytes, byteorder="little")

        attributes.append({
            "id": attr_id,
            "flags": flags,
            "current": current,
            "worst": worst,
            "raw_value": raw_value,
        })

        offset += _ATTR_RECORD_SIZE

    return attributes


def _parse_thresholds(data: bytes) -> dict[int, int]:
    """Распарсить 512-байтный буфер порогов SMART.

    Формат каждой записи (12 байт):
        [0]     Attribute ID
        [1]     Threshold value
        [2-11]  Reserved
    """
    thresholds = {}
    offset = _ATTR_DATA_OFFSET

    for i in range(_MAX_ATTRIBUTES):
        if offset + _ATTR_RECORD_SIZE > len(data):
            break

        attr_id = data[offset]
        if attr_id == 0:
            offset += _ATTR_RECORD_SIZE
            continue

        threshold = data[offset + 1]
        thresholds[attr_id] = threshold

        offset += _ATTR_RECORD_SIZE

    return thresholds


def _sat_smart_command(handle: DeviceHandle, feature: int, data_in: bool = True) -> bytes:
    """Отправить SMART-команду через ATA Pass-Through (для USB-SATA мостов).

    Используется IOCTL_ATA_PASS_THROUGH вместо legacy SMART IOCTL.
    USB-SATA мосты транслируют ATA-команды через SAT (SCSI-ATA Translation).

    Args:
        handle: Открытый DeviceHandle
        feature: SMART sub-command (SMART_READ_ATTRIBUTES и т.д.)
        data_in: True если ожидаем 512 байт данных в ответ

    Returns:
        512 байт SMART-данных (или пустой bytes для команд без данных)
    """
    header_size = ctypes.sizeof(ATA_PASS_THROUGH_EX)
    data_size = 512 if data_in else 0
    total_size = header_size + data_size

    buf = bytearray(total_size)

    # Заполняем ATA_PASS_THROUGH_EX через struct
    # Offset 0: Length (ushort)
    struct.pack_into("<H", buf, 0, header_size)
    # Offset 2: AtaFlags (ushort)
    flags = ATA_FLAGS_DRDY_REQUIRED
    if data_in:
        flags |= ATA_FLAGS_DATA_IN
    struct.pack_into("<H", buf, 2, flags)
    # Offset 4-7: PathId, TargetId, Lun, Reserved = 0 (уже нули)
    # Offset 8: DataTransferLength (ulong)
    struct.pack_into("<I", buf, 8, data_size)
    # Offset 12: TimeOutValue (ulong) — 10 секунд
    struct.pack_into("<I", buf, 12, 10)
    # Offset 16: ReservedAsUlong = 0
    # Offset 20 (x86) или 24 (x64): DataBufferOffset (ULONG_PTR)
    dbo_offset = ATA_PASS_THROUGH_EX.DataBufferOffset.offset
    ptr_size = ctypes.sizeof(ctypes.c_size_t)
    if ptr_size == 8:
        struct.pack_into("<Q", buf, dbo_offset, header_size)
    else:
        struct.pack_into("<I", buf, dbo_offset, header_size)

    # CurrentTaskFile — offset в структуре
    ctf_offset = ATA_PASS_THROUGH_EX.CurrentTaskFile.offset
    buf[ctf_offset + 0] = feature          # Features (SMART sub-command)
    buf[ctf_offset + 1] = 1                # Sector Count
    buf[ctf_offset + 2] = 0                # LBA Low
    buf[ctf_offset + 3] = SMART_CYL_LOW    # LBA Mid = 0x4F
    buf[ctf_offset + 4] = SMART_CYL_HI     # LBA High = 0xC2
    buf[ctf_offset + 5] = 0xA0             # Device/Head
    buf[ctf_offset + 6] = ATA_SMART_CMD    # Command = 0xB0

    result = handle.ioctl_raw(IOCTL_ATA_PASS_THROUGH, bytes(buf), total_size)

    if data_in and len(result) >= header_size + 512:
        return result[header_size:header_size + 512]
    return b""


def _scsi_sat_smart_command(handle: DeviceHandle, feature: int, data_in: bool = True) -> bytes:
    """Отправить SMART-команду через SCSI Pass-Through с SAT CDB.

    Использует ATA Pass-Through (16) CDB (opcode 0x85) поверх
    IOCTL_SCSI_PASS_THROUGH. Работает с большинством USB-SATA мостов,
    включая те, что не поддерживают IOCTL_ATA_PASS_THROUGH.
    """
    header_size = ctypes.sizeof(SCSI_PASS_THROUGH)
    sense_size = 32
    data_size = 512 if data_in else 0
    # Буфер: header + sense + data (выровнено по 8 байт)
    sense_offset = header_size
    data_offset = sense_offset + sense_size
    # Выравниваем data_offset по 8
    data_offset = (data_offset + 7) & ~7
    total_size = data_offset + data_size

    buf = bytearray(total_size)

    # SCSI_PASS_THROUGH header
    struct.pack_into("<H", buf, 0, header_size)                  # Length
    # ScsiStatus, PathId, TargetId, Lun = 0
    buf[6] = 16                                                   # CdbLength
    buf[7] = sense_size                                           # SenseInfoLength
    buf[8] = SCSI_IOCTL_DATA_IN if data_in else 0                # DataIn

    # DataTransferLength
    dtl_offset = SCSI_PASS_THROUGH.DataTransferLength.offset
    struct.pack_into("<I", buf, dtl_offset, data_size)

    # TimeOutValue
    tov_offset = SCSI_PASS_THROUGH.TimeOutValue.offset
    struct.pack_into("<I", buf, tov_offset, 10)

    # DataBufferOffset (ULONG_PTR)
    dbo_offset = SCSI_PASS_THROUGH.DataBufferOffset.offset
    ptr_size = ctypes.sizeof(ctypes.c_size_t)
    if ptr_size == 8:
        struct.pack_into("<Q", buf, dbo_offset, data_offset)
    else:
        struct.pack_into("<I", buf, dbo_offset, data_offset)

    # SenseInfoOffset
    sio_offset = SCSI_PASS_THROUGH.SenseInfoOffset.offset
    struct.pack_into("<I", buf, sio_offset, sense_offset)

    # CDB: ATA Pass-Through (16) — SAT command
    cdb_offset = SCSI_PASS_THROUGH.Cdb.offset
    buf[cdb_offset + 0] = 0x85          # ATA PASS-THROUGH (16) opcode
    # Protocol: PIO Data-In (4) for reads, non-data (3) for commands
    if data_in:
        buf[cdb_offset + 1] = (4 << 1)  # protocol = PIO Data-In, extend = 0
        buf[cdb_offset + 2] = 0x0E      # t_length=2(sector count), byt_blok=1, t_dir=1(from dev)
    else:
        buf[cdb_offset + 1] = (3 << 1)  # protocol = Non-data
        buf[cdb_offset + 2] = 0x20      # ck_cond = 1
    buf[cdb_offset + 4] = feature        # Features
    buf[cdb_offset + 6] = 1              # Sector Count
    buf[cdb_offset + 8] = 0              # LBA Low
    buf[cdb_offset + 10] = SMART_CYL_LOW # LBA Mid = 0x4F
    buf[cdb_offset + 12] = SMART_CYL_HI  # LBA High = 0xC2
    buf[cdb_offset + 13] = 0xA0          # Device
    buf[cdb_offset + 14] = ATA_SMART_CMD  # Command = 0xB0

    result = handle.ioctl_raw(IOCTL_SCSI_PASS_THROUGH, bytes(buf), total_size)

    if data_in and len(result) >= data_offset + 512:
        return result[data_offset:data_offset + 512]
    return b""


def read_smart_via_sat(handle: DeviceHandle) -> list[SmartAttribute]:
    """Прочитать SMART через ATA Pass-Through (для USB-дисков).

    Пробует два метода:
    1. IOCTL_ATA_PASS_THROUGH — простой, но не все USB-мосты поддерживают
    2. IOCTL_SCSI_PASS_THROUGH + SAT CDB — более универсальный

    Returns:
        Список SmartAttribute или пустой список если оба метода не работают.
    """
    # Выбираем функцию отправки команд: сначала ATA PT, потом SCSI SAT
    send_fn = None

    for method_name, fn in [
        ("ATA Pass-Through", _sat_smart_command),
        ("SCSI SAT", _scsi_sat_smart_command),
    ]:
        try:
            fn(handle, SMART_ENABLE_OPERATIONS, data_in=False)
            logger.debug(f"{method_name}: SMART ENABLE sent")
            send_fn = fn
            break
        except IoctlFailed:
            try:
                # Enable мог не пройти — пробуем сразу читать
                test = fn(handle, SMART_READ_ATTRIBUTES)
                if len(test) >= 362:  # минимум для хотя бы 1 атрибута
                    send_fn = fn
                    logger.info(f"{method_name}: works (enable skipped)")
                    break
            except IoctlFailed as e2:
                logger.debug(f"{method_name}: not supported ({e2})")
                continue

    if send_fn is None:
        logger.error("SAT: no supported pass-through method for this USB bridge")
        return []

    # 1. Read attributes
    try:
        attr_data = send_fn(handle, SMART_READ_ATTRIBUTES)
    except IoctlFailed as e:
        logger.error(f"SAT: SMART READ ATTRIBUTES failed: {e}")
        return []

    if len(attr_data) < 512:
        logger.error(f"SAT: SMART data too short: {len(attr_data)} bytes")
        return []

    raw_attrs = _parse_raw_attributes(attr_data)

    # 2. Read thresholds
    thresholds = {}
    try:
        thresh_data = send_fn(handle, SMART_READ_THRESHOLDS)
        if len(thresh_data) >= 512:
            thresholds = _parse_thresholds(thresh_data)
    except IoctlFailed as e:
        logger.warning(f"SAT: SMART READ THRESHOLDS failed: {e}")

    # 3. Assemble result (same logic as read_smart_attributes)
    result = []
    for attr in raw_attrs:
        attr_id = attr["id"]
        threshold = thresholds.get(attr_id, 0)
        current = attr["current"]
        is_critical = is_critical_attribute(attr_id)

        if threshold > 0 and current <= threshold:
            health = HealthLevel.CRITICAL
        elif threshold > 0 and current < 100 and current <= threshold + 10:
            health = HealthLevel.WARNING
        elif is_critical and (attr["raw_value"] & 0xFFFFFFFF) > 0 and attr_id in (5, 196, 197, 198):
            health = HealthLevel.WARNING
        else:
            health = HealthLevel.GOOD

        result.append(SmartAttribute(
            id=attr_id,
            name=get_attribute_name(attr_id),
            current=current,
            worst=attr["worst"],
            threshold=threshold,
            raw_value=attr["raw_value"],
            flags=attr["flags"],
            health_level=health,
        ))

    result.sort(key=lambda a: a.id)
    logger.info(f"SAT: read {len(result)} SMART attributes via ATA Pass-Through")
    return result


def read_smart_attributes(handle: DeviceHandle, drive_number: int = 0) -> list[SmartAttribute]:
    """Прочитать SMART-атрибуты и пороги для ATA/SATA диска.

    Args:
        handle: Открытый DeviceHandle
        drive_number: Номер диска (для логирования, bDriveNumber всегда 0)

    Returns:
        Список SmartAttribute с заполненными полями
    """
    out_size = ctypes.sizeof(SENDCMDOUTPARAMS)

    # 0. Включаем SMART (некоторые диски требуют явного enable)
    _enable_smart(handle)

    # 1. Читаем атрибуты
    cmd_attrs = _build_smart_command(SMART_READ_ATTRIBUTES)
    try:
        data_attrs = handle.ioctl(SMART_RCV_DRIVE_DATA, cmd_attrs, out_size)
    except IoctlFailed as e:
        logger.error(f"Не удалось прочитать SMART-атрибуты: {e}")
        return []

    # Пропускаем заголовок SENDCMDOUTPARAMS (cBufferSize + DriverStatus = 16 bytes)
    header_size = 4 + ctypes.sizeof(ctypes.c_ubyte) * 2 + ctypes.sizeof(ctypes.c_ubyte * 2) + ctypes.sizeof(ctypes.c_ulong * 2)
    # Проще: offset к bBuffer в SENDCMDOUTPARAMS
    buffer_offset = SENDCMDOUTPARAMS.bBuffer.offset
    if len(data_attrs) < buffer_offset + 512:
        logger.error(f"SMART data too short: {len(data_attrs)} bytes")
        return []

    attr_buffer = data_attrs[buffer_offset:buffer_offset + 512]
    raw_attrs = _parse_raw_attributes(attr_buffer)

    # 2. Читаем пороги
    cmd_thresh = _build_smart_command(SMART_READ_THRESHOLDS)
    thresholds = {}
    try:
        data_thresh = handle.ioctl(SMART_RCV_DRIVE_DATA, cmd_thresh, out_size)
        if len(data_thresh) >= buffer_offset + 512:
            thresh_buffer = data_thresh[buffer_offset:buffer_offset + 512]
            thresholds = _parse_thresholds(thresh_buffer)
    except IoctlFailed as e:
        logger.warning(f"Не удалось прочитать пороги SMART: {e}")

    # 3. Собираем результат
    result = []
    for attr in raw_attrs:
        attr_id = attr["id"]
        threshold = thresholds.get(attr_id, 0)
        current = attr["current"]
        worst = attr["worst"]
        is_critical = is_critical_attribute(attr_id)

        # Определяем уровень здоровья
        if threshold > 0 and current <= threshold:
            health = HealthLevel.CRITICAL
        elif threshold > 0 and current < 100 and current <= threshold + 10:
            health = HealthLevel.WARNING
        elif is_critical and (attr["raw_value"] & 0xFFFFFFFF) > 0 and attr_id in (5, 196, 197, 198):
            health = HealthLevel.WARNING
        else:
            health = HealthLevel.GOOD

        result.append(SmartAttribute(
            id=attr_id,
            name=get_attribute_name(attr_id),
            current=current,
            worst=worst,
            threshold=threshold,
            raw_value=attr["raw_value"],
            flags=attr["flags"],
            health_level=health,
        ))

    # Сортируем по ID
    result.sort(key=lambda a: a.id)
    return result


def detect_drive_type_from_smart(attributes: list[SmartAttribute]) -> DriveType:
    """Определить тип диска (SSD/HDD) по набору SMART-атрибутов.

    SSD определяется по наличию SSD-специфичных атрибутов (170-177, 231, 233, и т.д.)
    HDD определяется по наличию механических атрибутов (3=Spin-Up, 10=Spin Retry, и т.д.)
    """
    attr_ids = {a.id for a in attributes}

    ssd_count = len(attr_ids & SSD_INDICATOR_ATTRS)
    # Механические атрибуты: spin-up, spin retry, seek error, head flying
    hdd_indicators = {3, 10, 7, 189, 191, 220, 240}
    hdd_count = len(attr_ids & hdd_indicators)

    if ssd_count >= 2:
        return DriveType.SSD
    elif hdd_count >= 2:
        return DriveType.HDD
    else:
        return DriveType.UNKNOWN


def get_temperature_from_smart(attributes: list[SmartAttribute]) -> int | None:
    """Извлечь температуру из SMART-атрибутов.

    Temperature хранится в raw value:
    - Младший байт = текущая температура в °C
    - Некоторые вендоры (Kingston) пакуют min/max в старшие байты
    """
    for attr in attributes:
        if attr.id in (194, 190):  # Temperature, Airflow Temperature
            return attr.raw_value & 0xFF
    return None


# ============================================================================
# ATA IDENTIFY DEVICE через SAT — для USB-карманов, которые отдают
# generic-имя ("Mass Storage Device") в STORAGE_QUERY_PROPERTY
# ============================================================================


def _ata_string(data: bytes, offset: int, length: int) -> str:
    """Декодировать ATA-строку (байты в каждом 16-битном слове swap'аются).

    ATA IDENTIFY хранит ASCII в формате "слова big-endian": 'WD' = байты 'D','W'.
    Поля паддятся пробелами или NUL'ами — оба нужно убрать.
    """
    if offset + length > len(data) or length <= 0:
        return ""
    chunk = bytearray(data[offset:offset + length])
    for i in range(0, length - 1, 2):
        chunk[i], chunk[i + 1] = chunk[i + 1], chunk[i]
    text = chunk.decode("ascii", errors="replace")
    return text.strip().strip("\x00").strip()


def _scsi_inquiry_vpd_ata_info(handle: DeviceHandle) -> bytes:
    """Прочитать SCSI INQUIRY VPD page 0x89 (ATA Information).

    По спецификации SAT (SCSI/ATA Translation), мост обязан отдавать копию
    ответа ATA IDENTIFY DEVICE начиная с offset 60 страницы. Это работает
    на USB-мостах, которые не пропускают прямой ATA Pass-Through (16),
    но соблюдают SAT.

    Returns:
        Полный буфер VPD page 0x89 (>= 572 байт) или b"".
    """
    header_size = ctypes.sizeof(SCSI_PASS_THROUGH)
    sense_size = 32
    data_size = 572  # 60 (header) + 512 (IDENTIFY data)
    sense_offset = header_size
    data_offset = (sense_offset + sense_size + 7) & ~7
    total_size = data_offset + data_size

    buf = bytearray(total_size)

    struct.pack_into("<H", buf, 0, header_size)
    buf[6] = 12                         # CdbLength (INQUIRY = 6 или 12 байт)
    buf[7] = sense_size
    buf[8] = SCSI_IOCTL_DATA_IN

    struct.pack_into("<I", buf, SCSI_PASS_THROUGH.DataTransferLength.offset, data_size)
    struct.pack_into("<I", buf, SCSI_PASS_THROUGH.TimeOutValue.offset, 10)

    dbo_offset = SCSI_PASS_THROUGH.DataBufferOffset.offset
    if ctypes.sizeof(ctypes.c_size_t) == 8:
        struct.pack_into("<Q", buf, dbo_offset, data_offset)
    else:
        struct.pack_into("<I", buf, dbo_offset, data_offset)

    struct.pack_into("<I", buf, SCSI_PASS_THROUGH.SenseInfoOffset.offset, sense_offset)

    # CDB: SCSI INQUIRY (12 bytes max — opcode 0x12)
    cdb_offset = SCSI_PASS_THROUGH.Cdb.offset
    buf[cdb_offset + 0] = 0x12          # INQUIRY
    buf[cdb_offset + 1] = 0x01          # EVPD = 1 (запрашиваем VPD page)
    buf[cdb_offset + 2] = 0x89          # Page Code = ATA Information
    # Allocation Length (BE 16-bit) в bytes 3-4
    struct.pack_into(">H", buf, cdb_offset + 3, data_size)
    buf[cdb_offset + 5] = 0             # Control

    result = handle.ioctl_raw(IOCTL_SCSI_PASS_THROUGH, bytes(buf), total_size)

    if len(result) >= data_offset + 60:
        return result[data_offset:data_offset + data_size]
    return b""


def _scsi_sat_identify_device(handle: DeviceHandle) -> bytes:
    """Послать ATA IDENTIFY DEVICE (0xEC) через IOCTL_SCSI_PASS_THROUGH + SAT CDB.

    Returns:
        512 байт ответа IDENTIFY или b"" если не удалось.
    """
    header_size = ctypes.sizeof(SCSI_PASS_THROUGH)
    sense_size = 32
    data_size = 512
    sense_offset = header_size
    data_offset = (sense_offset + sense_size + 7) & ~7
    total_size = data_offset + data_size

    buf = bytearray(total_size)

    struct.pack_into("<H", buf, 0, header_size)        # Length
    buf[6] = 16                                         # CdbLength
    buf[7] = sense_size                                 # SenseInfoLength
    buf[8] = SCSI_IOCTL_DATA_IN                         # DataIn

    struct.pack_into("<I", buf, SCSI_PASS_THROUGH.DataTransferLength.offset, data_size)
    struct.pack_into("<I", buf, SCSI_PASS_THROUGH.TimeOutValue.offset, 10)

    dbo_offset = SCSI_PASS_THROUGH.DataBufferOffset.offset
    if ctypes.sizeof(ctypes.c_size_t) == 8:
        struct.pack_into("<Q", buf, dbo_offset, data_offset)
    else:
        struct.pack_into("<I", buf, dbo_offset, data_offset)

    struct.pack_into("<I", buf, SCSI_PASS_THROUGH.SenseInfoOffset.offset, sense_offset)

    # CDB: ATA Pass-Through (16) — IDENTIFY DEVICE (0xEC)
    cdb_offset = SCSI_PASS_THROUGH.Cdb.offset
    buf[cdb_offset + 0] = 0x85          # ATA PASS-THROUGH (16)
    buf[cdb_offset + 1] = (4 << 1)      # protocol = PIO Data-In
    buf[cdb_offset + 2] = 0x0E          # t_length=2(SC), byt_blok=1, t_dir=1
    buf[cdb_offset + 6] = 1              # Sector Count
    buf[cdb_offset + 13] = 0xA0          # Device
    buf[cdb_offset + 14] = 0xEC          # Command = IDENTIFY DEVICE

    result = handle.ioctl_raw(IOCTL_SCSI_PASS_THROUGH, bytes(buf), total_size)

    if len(result) >= data_offset + 512:
        return result[data_offset:data_offset + 512]
    return b""


def _ata_pt_identify_device(handle: DeviceHandle) -> bytes:
    """Послать ATA IDENTIFY DEVICE через IOCTL_ATA_PASS_THROUGH (без SCSI обёртки).

    Используется как первый вариант для USB-мостов, которые поддерживают ATA PT.
    """
    header_size = ctypes.sizeof(ATA_PASS_THROUGH_EX)
    data_size = 512
    total_size = header_size + data_size

    buf = bytearray(total_size)

    struct.pack_into("<H", buf, 0, header_size)               # Length
    struct.pack_into("<H", buf, 2,
                     ATA_FLAGS_DRDY_REQUIRED | ATA_FLAGS_DATA_IN)  # AtaFlags
    struct.pack_into("<I", buf, 8, data_size)                 # DataTransferLength
    struct.pack_into("<I", buf, 12, 10)                        # TimeOutValue (10s)

    dbo_offset = ATA_PASS_THROUGH_EX.DataBufferOffset.offset
    if ctypes.sizeof(ctypes.c_size_t) == 8:
        struct.pack_into("<Q", buf, dbo_offset, header_size)
    else:
        struct.pack_into("<I", buf, dbo_offset, header_size)

    ctf_offset = ATA_PASS_THROUGH_EX.CurrentTaskFile.offset
    buf[ctf_offset + 0] = 0       # Features
    buf[ctf_offset + 1] = 1       # Sector Count
    buf[ctf_offset + 2] = 0       # LBA Low
    buf[ctf_offset + 3] = 0       # LBA Mid (NOT SMART signature!)
    buf[ctf_offset + 4] = 0       # LBA High
    buf[ctf_offset + 5] = 0xA0    # Device/Head
    buf[ctf_offset + 6] = 0xEC    # Command = IDENTIFY DEVICE

    result = handle.ioctl_raw(IOCTL_ATA_PASS_THROUGH, bytes(buf), total_size)

    if len(result) >= header_size + 512:
        return result[header_size:header_size + 512]
    return b""


def _parse_identify_response(data: bytes) -> tuple[str, str, str]:
    """Распарсить 512 байт ответа IDENTIFY DEVICE → (model, serial, firmware).

    Words 10-19 (offset 20, len 20): Serial Number
    Words 23-26 (offset 46, len 8):  Firmware Revision
    Words 27-46 (offset 54, len 40): Model Number
    """
    if len(data) < 512:
        return ("", "", "")
    serial = _ata_string(data, 20, 20)
    firmware = _ata_string(data, 46, 8)
    model = _ata_string(data, 54, 40)
    return (model, serial, firmware)


def identify_device_via_sat(handle: DeviceHandle) -> tuple[str, str, str]:
    """Прочитать model/serial/firmware через цепочку SAT-запросов.

    Цепочка fallback:
      1. SCSI INQUIRY VPD page 0x89 (ATA Information) — самый надёжный для USB
      2. IOCTL_ATA_PASS_THROUGH с командой IDENTIFY DEVICE (0xEC)
      3. IOCTL_SCSI_PASS_THROUGH + SAT CDB (ATA Pass-Through 16) с 0xEC

    Используется для USB-карманов, у которых STORAGE_QUERY_PROPERTY возвращает
    обобщённое имя вроде "Mass Storage Device" вместо реальной модели.

    Returns:
        (model, serial, firmware) — или ("", "", "") если ничего не вышло.
    """
    # 1. VPD page 0x89: данные IDENTIFY DEVICE начинаются с offset 60
    try:
        vpd = _scsi_inquiry_vpd_ata_info(handle)
        if len(vpd) >= 60 + 512:
            page_code = vpd[1] if len(vpd) > 1 else 0
            if page_code == 0x89:
                identify = vpd[60:60 + 512]
                model, serial, firmware = _parse_identify_response(identify)
                if model:
                    logger.info(f"IDENTIFY via VPD 0x89: model='{model}'")
                    return (model, serial, firmware)
    except (IoctlFailed, OSError) as e:
        logger.debug(f"IDENTIFY via VPD 0x89 failed: {e}")

    # 2 & 3. Прямой IDENTIFY DEVICE (для мостов, которые пропускают ATA PT)
    for method_name, fn in (
        ("ATA PT", _ata_pt_identify_device),
        ("SCSI SAT", _scsi_sat_identify_device),
    ):
        try:
            data = fn(handle)
            if len(data) >= 512:
                model, serial, firmware = _parse_identify_response(data)
                if model:
                    logger.info(f"IDENTIFY via {method_name}: model='{model}'")
                    return (model, serial, firmware)
        except (IoctlFailed, OSError) as e:
            logger.debug(f"IDENTIFY via {method_name} failed: {e}")
            continue

    return ("", "", "")
