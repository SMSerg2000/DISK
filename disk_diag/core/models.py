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
    wmi_fallback: bool = False  # True = ограниченные данные через WMI


@dataclass
class HealthStatus:
    level: HealthLevel
    summary: str
    warnings: list[str] = field(default_factory=list)
    critical_issues: list[str] = field(default_factory=list)
    health_score: int = -1          # 0-100, -1 = не рассчитан
    tbw_consumed_tb: float = -1     # -1 = не известно
    tbw_rated_tb: float = -1        # -1 = не известно
    tbw_remaining_days: int = -1    # прогноз, -1 = не известно
    daily_write_tb: float = -1      # среднесуточная запись
    waf: float = -1                 # Write Amplification Factor, -1 = нет данных


@dataclass
class BenchmarkResult:
    """Результаты бенчмарка."""
    # Sequential read
    sequential_speed_mbps: float = 0.0
    sequential_bytes_read: int = 0
    sequential_time_sec: float = 0.0

    # Sequential write
    seq_write_speed_mbps: float = 0.0
    seq_write_bytes: int = 0
    seq_write_time_sec: float = 0.0

    # Random 4K read
    random_iops: float = 0.0
    random_avg_latency_us: float = 0.0
    random_min_latency_us: float = 0.0
    random_max_latency_us: float = 0.0
    random_p95_latency_us: float = 0.0
    random_p99_latency_us: float = 0.0
    random_reads_count: int = 0

    # Random 4K write
    random_write_iops: float = 0.0
    random_write_avg_latency_us: float = 0.0
    random_write_count: int = 0

    # Scatter plot data: (offset_gb, latency_us)
    latency_points: list[tuple[float, float]] = field(default_factory=list)

    # Full Drive Read Sweep: (position_gb, speed_mbps)
    sweep_points: list[tuple[float, float]] = field(default_factory=list)

    # SLC Cache test
    slc_cache_size_gb: float = 0.0
    slc_speed_mbps: float = 0.0
    slc_post_cache_speed_mbps: float = 0.0
    slc_points: list[tuple[float, float]] = field(default_factory=list)

    # Mixed I/O (70/30)
    mixed_read_iops: float = 0.0
    mixed_write_iops: float = 0.0
    mixed_total_iops: float = 0.0
    mixed_count: int = 0

    # Write-Read-Verify
    verify_blocks_tested: int = 0
    verify_blocks_ok: int = 0
    verify_blocks_failed: int = 0
    verify_speed_mbps: float = 0.0

    # Temperature log: (elapsed_sec, temp_celsius)
    temp_log: list[tuple[float, float]] = field(default_factory=list)


class ScanMode(Enum):
    """Режим сканирования поверхности (как в Victoria HDD)."""
    IGNORE = "ignore"    # только чтение
    ERASE = "erase"      # запись нулей (при ошибке, опционально + медленные)
    REFRESH = "refresh"  # чтение → перезапись тех же данных
    WRITE = "write"      # запись нулей на ВСЮ поверхность (полное стирание)


class BlockCategory(Enum):
    """Категория времени отклика блока (как в Victoria HDD)."""
    PENDING = 0       # ещё не просканирован
    EXCELLENT = 1     # < 5ms
    GOOD = 2          # < 20ms
    ACCEPTABLE = 3    # < 50ms
    SLOW = 4          # < 150ms
    VERY_SLOW = 5     # < 500ms
    CRITICAL = 6      # >= 500ms
    ERROR = 7         # ошибка чтения

    @staticmethod
    def from_latency_ms(latency_ms: float) -> "BlockCategory":
        if latency_ms < 5:
            return BlockCategory.EXCELLENT
        elif latency_ms < 20:
            return BlockCategory.GOOD
        elif latency_ms < 50:
            return BlockCategory.ACCEPTABLE
        elif latency_ms < 150:
            return BlockCategory.SLOW
        elif latency_ms < 500:
            return BlockCategory.VERY_SLOW
        else:
            return BlockCategory.CRITICAL


@dataclass
class SurfaceScanResult:
    """Результаты сканирования поверхности."""
    total_blocks: int = 0
    scanned_blocks: int = 0
    error_count: int = 0
    error_offsets: list[int] = field(default_factory=list)
    counts: dict[int, int] = field(default_factory=dict)  # BlockCategory.value → count
    elapsed_sec: float = 0.0
    avg_speed_mbps: float = 0.0
    repaired_blocks: int = 0    # успешно перезаписанных блоков
    write_errors: int = 0       # ошибки записи
    bad_sector_lbas: list[int] = field(default_factory=list)  # LBA нечитаемых секторов
