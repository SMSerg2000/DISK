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
        "Критические предупреждения — битовая маска: резерв, температура, надёжность, режим read-only, энергонезависимая память",
        is_critical=True,
    ),
    "temperature_celsius": NvmeFieldInfo(
        "Temperature",
        "Температура — текущая температура контроллера NVMe в градусах Цельсия",
        "°C",
    ),
    "available_spare": NvmeFieldInfo(
        "Available Spare",
        "Доступный резерв — процент оставшихся резервных блоков для замены изношенных",
        "%",
        is_critical=True,
    ),
    "available_spare_threshold": NvmeFieldInfo(
        "Available Spare Threshold",
        "Порог резерва — при падении Available Spare ниже этого значения контроллер выдаст предупреждение",
        "%",
    ),
    "percentage_used": NvmeFieldInfo(
        "Percentage Used",
        "Износ — оценка использованного ресурса диска в процентах (может превышать 100%)",
        "%",
    ),
    "data_units_read": NvmeFieldInfo(
        "Data Units Read",
        "Прочитано данных — общий объём прочитанных данных (1 unit = 512 КБ = 1000 × 512 байт)",
    ),
    "data_units_written": NvmeFieldInfo(
        "Data Units Written",
        "Записано данных — общий объём записанных данных (1 unit = 512 КБ = 1000 × 512 байт)",
    ),
    "host_read_commands": NvmeFieldInfo(
        "Host Read Commands",
        "Команды чтения — количество команд чтения, полученных контроллером от хоста",
    ),
    "host_write_commands": NvmeFieldInfo(
        "Host Write Commands",
        "Команды записи — количество команд записи, полученных контроллером от хоста",
    ),
    "controller_busy_time": NvmeFieldInfo(
        "Controller Busy Time",
        "Время занятости — суммарное время, когда контроллер был занят обработкой I/O-команд",
        "min",
    ),
    "power_cycles": NvmeFieldInfo(
        "Power Cycles",
        "Циклы питания — количество включений/выключений диска",
    ),
    "power_on_hours": NvmeFieldInfo(
        "Power-On Hours",
        "Наработка — общее время работы диска во включённом состоянии",
        "hours",
    ),
    "unsafe_shutdowns": NvmeFieldInfo(
        "Unsafe Shutdowns",
        "Аварийные выключения — количество выключений без корректного сброса кэша (flush). Частые аварийные выключения могут повредить данные",
    ),
    "media_errors": NvmeFieldInfo(
        "Media and Data Integrity Errors",
        "Ошибки носителя — количество неисправимых ошибок целостности данных. Любое значение >0 — повод для беспокойства",
        is_critical=True,
    ),
    "error_log_entries": NvmeFieldInfo(
        "Error Log Entries",
        "Записи в журнале ошибок — общее количество записей в журнале ошибок контроллера",
    ),
    "warning_temp_time": NvmeFieldInfo(
        "Warning Composite Temperature Time",
        "Время перегрева (предупреждение) — суммарное время работы при температуре выше порога предупреждения",
        "min",
    ),
    "critical_temp_time": NvmeFieldInfo(
        "Critical Composite Temperature Time",
        "Время перегрева (критическое) — суммарное время работы при критически высокой температуре. Может привести к троттлингу или аварийному отключению",
        "min",
        is_critical=True,
    ),
}
