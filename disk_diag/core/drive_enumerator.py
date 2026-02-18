"""Перечисление физических дисков и получение базовой информации."""

import ctypes
import logging
import struct

from .constants import (
    IOCTL_STORAGE_QUERY_PROPERTY,
    IOCTL_DISK_GET_DRIVE_GEOMETRY_EX,
    SMART_GET_VERSION,
    StorageDeviceProperty,
    PropertyStandardQuery,
    BUS_TYPE_NAMES,
    BusTypeNvme, BusTypeSata, BusTypeAta, BusTypeUsb,
    MAX_PHYSICAL_DRIVES,
)
from .structures import (
    STORAGE_PROPERTY_QUERY,
    GETVERSIONINPARAMS,
    DISK_GEOMETRY_EX,
)
from .winapi import DeviceHandle, DriveNotFound, AdminPrivilegeRequired, IoctlFailed
from .models import DriveInfo, DriveType, InterfaceType

logger = logging.getLogger(__name__)


def _extract_string(buffer: bytes, offset: int) -> str:
    """Извлечь C-строку из буфера по смещению."""
    if offset == 0 or offset >= len(buffer):
        return ""
    end = buffer.index(0, offset) if 0 in buffer[offset:] else len(buffer)
    return buffer[offset:end].decode("ascii", errors="replace").strip()


def _bus_type_to_interface(bus_type: int) -> InterfaceType:
    """Преобразовать STORAGE_BUS_TYPE в InterfaceType."""
    if bus_type == BusTypeNvme:
        return InterfaceType.NVME
    elif bus_type == BusTypeSata:
        return InterfaceType.SATA
    elif bus_type == BusTypeAta:
        return InterfaceType.ATA
    elif bus_type == BusTypeUsb:
        return InterfaceType.USB
    else:
        return InterfaceType.UNKNOWN


def _get_device_descriptor(handle: DeviceHandle) -> tuple[str, str, str, int]:
    """Получить модель, серийник, прошивку и тип шины через STORAGE_QUERY_PROPERTY.

    Returns:
        (model, serial, firmware, bus_type)
    """
    query = STORAGE_PROPERTY_QUERY()
    query.PropertyId = StorageDeviceProperty
    query.QueryType = PropertyStandardQuery

    logger.debug(
        f"STORAGE_PROPERTY_QUERY sizeof={ctypes.sizeof(query)}, "
        f"PropertyId={query.PropertyId}, QueryType={query.QueryType}"
    )

    out_size = 4096
    data = handle.ioctl(IOCTL_STORAGE_QUERY_PROPERTY, query, out_size)
    logger.debug(f"STORAGE_QUERY_PROPERTY returned {len(data)} bytes")

    if len(data) < 40:
        return ("Unknown", "", "", 0)

    # Разбираем заголовок STORAGE_DEVICE_DESCRIPTOR
    # Offsets: Version(4) + Size(4) + DeviceType(1) + Modifier(1) + Removable(1) +
    #          CmdQueue(1) + VendorIdOff(4) + ProductIdOff(4) + ProductRevOff(4) +
    #          SerialOff(4) + BusType(4) + RawPropsLen(4)
    (version, size, dev_type, modifier, removable, cmd_queue,
     vendor_off, product_off, rev_off, serial_off, bus_type, raw_len) = \
        struct.unpack_from("<IIBBBBI I I I I I", data, 0)

    model_parts = []
    if vendor_off > 0 and vendor_off < len(data):
        vendor = _extract_string(data, vendor_off)
        if vendor:
            model_parts.append(vendor)
    if product_off > 0 and product_off < len(data):
        product = _extract_string(data, product_off)
        if product:
            model_parts.append(product)
    model = " ".join(model_parts) if model_parts else "Unknown"

    serial = _extract_string(data, serial_off) if serial_off > 0 else ""
    firmware = _extract_string(data, rev_off) if rev_off > 0 else ""

    return (model, serial, firmware, bus_type)


def _get_capacity(handle: DeviceHandle) -> int:
    """Получить ёмкость диска в байтах через IOCTL_DISK_GET_DRIVE_GEOMETRY_EX."""
    try:
        data = handle.ioctl(IOCTL_DISK_GET_DRIVE_GEOMETRY_EX, None, 256)
        if len(data) >= 32:
            # DISK_GEOMETRY (24 bytes) + DiskSize (8 bytes)
            disk_size = struct.unpack_from("<Q", data, 24)[0]
            return disk_size
    except IoctlFailed:
        pass
    return 0


def _check_smart_support(handle: DeviceHandle) -> tuple[bool, bool]:
    """Проверить поддержку SMART через SMART_GET_VERSION.

    Returns:
        (smart_supported, smart_enabled)
    """
    try:
        data = handle.ioctl(SMART_GET_VERSION, None, ctypes.sizeof(GETVERSIONINPARAMS))
        if len(data) >= ctypes.sizeof(GETVERSIONINPARAMS):
            ver = GETVERSIONINPARAMS.from_buffer_copy(data)
            # fCapabilities bit 0 = SMART supported
            supported = bool(ver.fCapabilities & 0x01)
            return (supported, supported)  # Если поддерживается, обычно уже включён
    except IoctlFailed as e:
        logger.debug(f"SMART_GET_VERSION failed: {e}")
    return (False, False)


def enumerate_drives() -> list[DriveInfo]:
    """Сканировать PhysicalDrive0..15 и вернуть список обнаруженных дисков."""
    drives = []

    for n in range(MAX_PHYSICAL_DRIVES):
        try:
            # Сначала пробуем read-only — достаточно для перечисления
            with DeviceHandle(n, read_only=True) as h:
                model, serial, firmware, bus_type = _get_device_descriptor(h)
                capacity = _get_capacity(h)
                interface = _bus_type_to_interface(bus_type)

                # SMART support check (только для ATA/SATA дисков)
                smart_supported = False
                smart_enabled = False
                if interface in (InterfaceType.SATA, InterfaceType.ATA):
                    smart_supported, smart_enabled = _check_smart_support(h)
                elif interface == InterfaceType.NVME:
                    smart_supported = True
                    smart_enabled = True

                # Определение SSD vs HDD
                if interface == InterfaceType.NVME:
                    drive_type = DriveType.SSD
                else:
                    drive_type = DriveType.UNKNOWN  # Уточним после чтения SMART

                drive = DriveInfo(
                    drive_number=n,
                    model=model,
                    serial_number=serial,
                    firmware_revision=firmware,
                    capacity_bytes=capacity,
                    interface_type=interface,
                    drive_type=drive_type,
                    bus_type_raw=bus_type,
                    smart_supported=smart_supported,
                    smart_enabled=smart_enabled,
                )
                drives.append(drive)
                logger.info(f"Found: {drive.display_name}")

        except DriveNotFound:
            logger.debug(f"PhysicalDrive{n}: not found")
            continue
        except AdminPrivilegeRequired:
            logger.warning(f"PhysicalDrive{n}: access denied (need admin)")
            continue
        except Exception as e:
            logger.warning(f"PhysicalDrive{n}: {type(e).__name__}: {e}")
            continue

    return drives
