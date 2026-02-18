"""Модели данных для передачи между слоями приложения."""

from dataclasses import dataclass, field
from enum import Enum


class DriveType(Enum):
    HDD = "HDD"
    SSD = "SSD"
    UNKNOWN = "Unknown"


class InterfaceType(Enum):
    SATA = "SATA"
    NVME = "NVMe"
    USB = "USB"
    SCSI = "SCSI"
    ATA = "ATA"
    UNKNOWN = "Unknown"


class HealthLevel(Enum):
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class DriveInfo:
    drive_number: int
    model: str
    serial_number: str
    firmware_revision: str
    capacity_bytes: int
    interface_type: InterfaceType
    drive_type: DriveType
    bus_type_raw: int
    rotation_rate: int = 0  # 0=unknown, 1=SSD(non-rotating), >1=RPM
    smart_supported: bool = False
    smart_enabled: bool = False

    @property
    def display_name(self) -> str:
        size_gb = self.capacity_bytes / (1024 ** 3)
        return f"Disk {self.drive_number}: {self.model.strip()} ({size_gb:.1f} GB, {self.interface_type.value})"


@dataclass
class SmartAttribute:
    id: int
    name: str
    current: int
    worst: int
    threshold: int
    raw_value: int
    flags: int
    health_level: HealthLevel = HealthLevel.UNKNOWN

    @property
    def is_prefail(self) -> bool:
        return bool(self.flags & 0x01)


@dataclass
class NvmeHealthInfo:
    critical_warning: int
    temperature_celsius: int
    available_spare: int
    available_spare_threshold: int
    percentage_used: int
    data_units_read: int
    data_units_written: int
    host_read_commands: int
    host_write_commands: int
    controller_busy_time: int  # minutes
    power_cycles: int
    power_on_hours: int
    unsafe_shutdowns: int
    media_errors: int
    error_log_entries: int
    warning_temp_time: int  # minutes
    critical_temp_time: int  # minutes
    temperature_sensors: list[int] = field(default_factory=list)  # Celsius


@dataclass
class HealthStatus:
    level: HealthLevel
    summary: str
    warnings: list[str] = field(default_factory=list)
    critical_issues: list[str] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    """Результаты бенчмарка."""
    # Sequential read
    sequential_speed_mbps: float = 0.0
    sequential_bytes_read: int = 0
    sequential_time_sec: float = 0.0

    # Random 4K read
    random_iops: float = 0.0
    random_avg_latency_us: float = 0.0
    random_min_latency_us: float = 0.0
    random_max_latency_us: float = 0.0
    random_reads_count: int = 0

    # Scatter plot data: (offset_gb, latency_us)
    latency_points: list[tuple[float, float]] = field(default_factory=list)
