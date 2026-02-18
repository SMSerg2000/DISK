"""Чтение NVMe SMART / Health Info через Windows IOCTL."""

import ctypes
import logging
import struct

from .constants import (
    IOCTL_STORAGE_QUERY_PROPERTY,
    StorageDeviceProtocolSpecificProperty,
    PropertyStandardQuery,
    ProtocolTypeNvme,
    NVMeDataTypeLogPage,
    NVME_LOG_PAGE_HEALTH_INFO,
)
from .structures import NVME_HEALTH_INFO_LOG
from .winapi import DeviceHandle, IoctlFailed
from .models import NvmeHealthInfo

logger = logging.getLogger(__name__)

# Размер NVME_HEALTH_INFO_LOG
_HEALTH_LOG_SIZE = 512

# Смещение данных протокола от начала запроса
_PROTOCOL_DATA_OFFSET = 40  # sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA)


def _parse_128bit(data: bytes | list) -> int:
    """Преобразовать 16-байтное little-endian значение в Python int."""
    if isinstance(data, (list, tuple)):
        data = bytes(data)
    return int.from_bytes(data[:16], byteorder="little")


def read_nvme_health(handle: DeviceHandle) -> NvmeHealthInfo:
    """Прочитать NVMe SMART/Health Information Log Page.

    Использует IOCTL_STORAGE_QUERY_PROPERTY с протокол-специфичным запросом
    для получения NVMe Health Info (Log Page 02h).
    """
    # Формируем буфер запроса:
    # STORAGE_PROPERTY_QUERY (PropertyId + QueryType) +
    # STORAGE_PROTOCOL_SPECIFIC_DATA + место для ответа
    #
    # Структура запроса (вручную, т.к. нужен точный контроль над layout):
    # [0:4]   PropertyId = StorageDeviceProtocolSpecificProperty (50)
    # [4:8]   QueryType  = PropertyStandardQuery (0)
    # [8:12]  ProtocolType = ProtocolTypeNvme (3)
    # [12:16] DataType = NVMeDataTypeLogPage (2)
    # [16:20] ProtocolDataRequestValue = NVME_LOG_PAGE_HEALTH_INFO (0x02)
    # [20:24] ProtocolDataRequestSubValue = 0
    # [24:28] ProtocolDataOffset = sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA) = 44
    # [28:32] ProtocolDataLength = 512
    # [32:36] FixedProtocolReturnData = 0
    # [36:40] ProtocolDataRequestSubValue2 = 0
    # [40:44] ProtocolDataRequestSubValue3 = 0
    # [44:48] ProtocolDataRequestSubValue4 = 0  (Reserved)
    # [48:52] ProtocolDataRequestSubValue5 = 0  (Reserved)

    # sizeof(STORAGE_PROTOCOL_SPECIFIC_DATA) = 11 * 4 = 44 bytes
    proto_data_size = 44
    query_size = 8 + proto_data_size  # PropertyId(4) + QueryType(4) + proto_data

    buf = bytearray(query_size)
    struct.pack_into("<I", buf, 0, StorageDeviceProtocolSpecificProperty)  # PropertyId
    struct.pack_into("<I", buf, 4, PropertyStandardQuery)                  # QueryType
    struct.pack_into("<I", buf, 8, ProtocolTypeNvme)                       # ProtocolType
    struct.pack_into("<I", buf, 12, NVMeDataTypeLogPage)                   # DataType
    struct.pack_into("<I", buf, 16, NVME_LOG_PAGE_HEALTH_INFO)             # RequestValue
    struct.pack_into("<I", buf, 20, 0)                                     # RequestSubValue
    struct.pack_into("<I", buf, 24, proto_data_size)                       # DataOffset
    struct.pack_into("<I", buf, 28, _HEALTH_LOG_SIZE)                      # DataLength

    # Размер выходного буфера: заголовок STORAGE_PROTOCOL_DATA_DESCRIPTOR + данные
    # STORAGE_PROTOCOL_DATA_DESCRIPTOR: Version(4) + Size(4) + STORAGE_PROTOCOL_SPECIFIC_DATA(44) = 52
    descriptor_size = 8 + proto_data_size
    out_size = descriptor_size + _HEALTH_LOG_SIZE

    try:
        result = handle.ioctl_raw(IOCTL_STORAGE_QUERY_PROPERTY, bytes(buf), out_size)
    except IoctlFailed as e:
        logger.error(f"NVMe health query failed: {e}")
        raise

    # Данные Health Log начинаются после дескриптора
    health_offset = descriptor_size
    if len(result) < health_offset + _HEALTH_LOG_SIZE:
        # Попробуем взять столько, сколько есть
        logger.warning(
            f"NVMe health response shorter than expected: {len(result)} bytes "
            f"(need {health_offset + _HEALTH_LOG_SIZE})"
        )

    health_data = result[health_offset:]
    if len(health_data) < 200:  # Минимум для основных полей
        raise IoctlFailed("NVMe Health Parse", 0, "Response too short")

    # Парсим по структуре NVME_HEALTH_INFO_LOG
    critical_warning = health_data[0]

    # Temperature: 2 bytes little-endian, в Кельвинах
    temp_kelvin = struct.unpack_from("<H", health_data, 1)[0]
    temp_celsius = temp_kelvin - 273

    available_spare = health_data[3]
    available_spare_threshold = health_data[4]
    percentage_used = health_data[5]

    # 128-bit поля начинаются с offset 32
    offset = 32
    data_units_read = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    data_units_written = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    host_read_commands = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    host_write_commands = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    controller_busy_time = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    power_cycles = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    power_on_hours = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    unsafe_shutdowns = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    media_errors = _parse_128bit(health_data[offset:offset + 16])
    offset += 16
    error_log_entries = _parse_128bit(health_data[offset:offset + 16])
    offset += 16

    # Warning/Critical temperature time (4 bytes each)
    warning_temp_time = struct.unpack_from("<I", health_data, offset)[0]
    offset += 4
    critical_temp_time = struct.unpack_from("<I", health_data, offset)[0]
    offset += 4

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
