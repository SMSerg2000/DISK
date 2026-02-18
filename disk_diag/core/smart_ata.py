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
)
from .structures import SENDCMDINPARAMS, SENDCMDOUTPARAMS, IDEREGS
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
        elif threshold > 0 and current <= threshold + 10:
            health = HealthLevel.WARNING
        elif is_critical and attr["raw_value"] > 0 and attr_id in (5, 196, 197, 198):
            # Для критических атрибутов — raw > 0 уже повод для warning
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
