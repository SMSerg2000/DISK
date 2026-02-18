"""ctypes-структуры для Windows IOCTL вызовов."""

import ctypes
from ctypes import Structure, c_ubyte, c_ushort, c_ulong, c_ulonglong


# ============================================================
# ATA / SMART structures — _pack_ = 1 (фиксированный бинарный формат)
# ============================================================

class IDEREGS(Structure):
    _pack_ = 1
    _fields_ = [
        ("bFeaturesReg", c_ubyte),
        ("bSectorCountReg", c_ubyte),
        ("bSectorNumberReg", c_ubyte),
        ("bCylLowReg", c_ubyte),
        ("bCylHighReg", c_ubyte),
        ("bDriveHeadReg", c_ubyte),
        ("bCommandReg", c_ubyte),
        ("bReserved", c_ubyte),
    ]


class DRIVERSTATUS(Structure):
    _pack_ = 1
    _fields_ = [
        ("bDriverError", c_ubyte),
        ("bIDEError", c_ubyte),
        ("bReserved", c_ubyte * 2),
        ("dwReserved", c_ulong * 2),
    ]


class SENDCMDINPARAMS(Structure):
    _pack_ = 1
    _fields_ = [
        ("cBufferSize", c_ulong),
        ("irDriveRegs", IDEREGS),
        ("bDriveNumber", c_ubyte),
        ("bReserved", c_ubyte * 3),
        ("dwReserved", c_ulong * 4),
        ("bBuffer", c_ubyte * 1),
    ]


class SENDCMDOUTPARAMS(Structure):
    _pack_ = 1
    _fields_ = [
        ("cBufferSize", c_ulong),
        ("DriverStatus", DRIVERSTATUS),
        ("bBuffer", c_ubyte * 512),
    ]


class GETVERSIONINPARAMS(Structure):
    """Результат SMART_GET_VERSION."""
    _pack_ = 1
    _fields_ = [
        ("bVersion", c_ubyte),
        ("bRevision", c_ubyte),
        ("bReserved", c_ubyte),
        ("bIDEDeviceMap", c_ubyte),
        ("fCapabilities", c_ulong),
        ("dwReserved", c_ulong * 4),
    ]


# ============================================================
# Storage Query Property structures — нативное выравнивание Windows
# ============================================================

class STORAGE_PROPERTY_QUERY(Structure):
    _fields_ = [
        ("PropertyId", c_ulong),
        ("QueryType", c_ulong),
        ("AdditionalParameters", c_ubyte * 4),  # минимум 4 байта для выравнивания
    ]


class STORAGE_DEVICE_DESCRIPTOR(Structure):
    """Заголовок дескриптора устройства (переменной длины)."""
    _fields_ = [
        ("Version", c_ulong),
        ("Size", c_ulong),
        ("DeviceType", c_ubyte),
        ("DeviceTypeModifier", c_ubyte),
        ("RemovableMedia", c_ubyte),
        ("CommandQueueing", c_ubyte),
        ("VendorIdOffset", c_ulong),
        ("ProductIdOffset", c_ulong),
        ("ProductRevisionOffset", c_ulong),
        ("SerialNumberOffset", c_ulong),
        ("BusType", c_ulong),
        ("RawPropertiesLength", c_ulong),
        ("RawDeviceProperties", c_ubyte * 1),
    ]


# ============================================================
# NVMe protocol-specific query structures
# ============================================================

class STORAGE_PROTOCOL_SPECIFIC_DATA(Structure):
    _fields_ = [
        ("ProtocolType", c_ulong),
        ("DataType", c_ulong),
        ("ProtocolDataRequestValue", c_ulong),
        ("ProtocolDataRequestSubValue", c_ulong),
        ("ProtocolDataOffset", c_ulong),
        ("ProtocolDataLength", c_ulong),
        ("FixedProtocolReturnData", c_ulong),
        ("ProtocolDataRequestSubValue2", c_ulong),
        ("ProtocolDataRequestSubValue3", c_ulong),
        ("ProtocolDataRequestSubValue4", c_ulong),
        ("ProtocolDataRequestSubValue5", c_ulong),
    ]


class STORAGE_PROTOCOL_DATA_DESCRIPTOR(Structure):
    _fields_ = [
        ("Version", c_ulong),
        ("Size", c_ulong),
        ("ProtocolSpecificData", STORAGE_PROTOCOL_SPECIFIC_DATA),
    ]


# ============================================================
# NVMe Health Info Log (512 bytes) — строгий бинарный формат
# ============================================================

class NVME_HEALTH_INFO_LOG(Structure):
    _pack_ = 1
    _fields_ = [
        ("CriticalWarning", c_ubyte),
        ("Temperature", c_ubyte * 2),
        ("AvailableSpare", c_ubyte),
        ("AvailableSpareThreshold", c_ubyte),
        ("PercentageUsed", c_ubyte),
        ("Reserved0", c_ubyte * 26),
        ("DataUnitRead", c_ubyte * 16),
        ("DataUnitWritten", c_ubyte * 16),
        ("HostReadCommands", c_ubyte * 16),
        ("HostWrittenCommands", c_ubyte * 16),
        ("ControllerBusyTime", c_ubyte * 16),
        ("PowerCycle", c_ubyte * 16),
        ("PowerOnHours", c_ubyte * 16),
        ("UnsafeShutdowns", c_ubyte * 16),
        ("MediaErrors", c_ubyte * 16),
        ("ErrorInfoLogEntryCount", c_ubyte * 16),
        ("WarningCompositeTemperatureTime", c_ulong),
        ("CriticalCompositeTemperatureTime", c_ulong),
        ("TemperatureSensor1", c_ushort),
        ("TemperatureSensor2", c_ushort),
        ("TemperatureSensor3", c_ushort),
        ("TemperatureSensor4", c_ushort),
        ("TemperatureSensor5", c_ushort),
        ("TemperatureSensor6", c_ushort),
        ("TemperatureSensor7", c_ushort),
        ("TemperatureSensor8", c_ushort),
        ("Reserved1", c_ubyte * 296),
    ]


# ============================================================
# Disk Geometry — нативное выравнивание
# ============================================================

class DISK_GEOMETRY(Structure):
    _fields_ = [
        ("Cylinders", c_ulonglong),
        ("MediaType", c_ulong),
        ("TracksPerCylinder", c_ulong),
        ("SectorsPerTrack", c_ulong),
        ("BytesPerSector", c_ulong),
    ]


class DISK_GEOMETRY_EX(Structure):
    _fields_ = [
        ("Geometry", DISK_GEOMETRY),
        ("DiskSize", c_ulonglong),
        ("Data", c_ubyte * 1),
    ]
