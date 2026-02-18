"""Описания полей NVMe Health Info Log."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class NvmeFieldInfo:
    name: str
    description: str
    unit: Optional[str] = None
    is_critical: bool = False


NVME_HEALTH_FIELDS: dict[str, NvmeFieldInfo] = {
    "critical_warning": NvmeFieldInfo(
        "Critical Warning",
        "Битовая маска критических предупреждений",
        is_critical=True,
    ),
    "temperature_celsius": NvmeFieldInfo(
        "Temperature",
        "Текущая температура контроллера",
        "°C",
    ),
    "available_spare": NvmeFieldInfo(
        "Available Spare",
        "Процент оставшихся резервных блоков",
        "%",
        is_critical=True,
    ),
    "available_spare_threshold": NvmeFieldInfo(
        "Available Spare Threshold",
        "Порог для предупреждения о нехватке резерва",
        "%",
    ),
    "percentage_used": NvmeFieldInfo(
        "Percentage Used",
        "Процент использованного ресурса (может быть >100%)",
        "%",
    ),
    "data_units_read": NvmeFieldInfo(
        "Data Units Read",
        "Количество прочитанных блоков по 512 КБ",
    ),
    "data_units_written": NvmeFieldInfo(
        "Data Units Written",
        "Количество записанных блоков по 512 КБ",
    ),
    "host_read_commands": NvmeFieldInfo(
        "Host Read Commands",
        "Количество команд чтения от хоста",
    ),
    "host_write_commands": NvmeFieldInfo(
        "Host Write Commands",
        "Количество команд записи от хоста",
    ),
    "controller_busy_time": NvmeFieldInfo(
        "Controller Busy Time",
        "Время занятости контроллера обработкой I/O",
        "min",
    ),
    "power_cycles": NvmeFieldInfo(
        "Power Cycles",
        "Количество циклов включения/выключения",
    ),
    "power_on_hours": NvmeFieldInfo(
        "Power-On Hours",
        "Общее время работы",
        "hours",
    ),
    "unsafe_shutdowns": NvmeFieldInfo(
        "Unsafe Shutdowns",
        "Количество аварийных выключений (без flush)",
    ),
    "media_errors": NvmeFieldInfo(
        "Media and Data Integrity Errors",
        "Количество неисправимых ошибок целостности данных",
        is_critical=True,
    ),
    "error_log_entries": NvmeFieldInfo(
        "Error Log Entries",
        "Количество записей в журнале ошибок",
    ),
    "warning_temp_time": NvmeFieldInfo(
        "Warning Composite Temperature Time",
        "Время в состоянии предупреждения о перегреве",
        "min",
    ),
    "critical_temp_time": NvmeFieldInfo(
        "Critical Composite Temperature Time",
        "Время в состоянии критического перегрева",
        "min",
        is_critical=True,
    ),
}
