"""Описания полей NVMe Health Info Log."""

from dataclasses import dataclass
from typing import Optional
from ..i18n import tr


@dataclass(frozen=True)
class NvmeFieldInfo:
    name_en: str
    name_ru: str
    desc_en: str
    desc_ru: str
    unit: Optional[str] = None
    is_critical: bool = False

    @property
    def name(self) -> str:
        return tr(self.name_en, self.name_ru)

    @property
    def description(self) -> str:
        return tr(self.desc_en, self.desc_ru)


def _f(name_en, name_ru, desc_en, desc_ru, unit=None, is_critical=False):
    return NvmeFieldInfo(name_en, name_ru, desc_en, desc_ru, unit, is_critical)


NVME_HEALTH_FIELDS: dict[str, NvmeFieldInfo] = {
    "critical_warning": _f(
        "Critical Warning", "Критические предупреждения",
        "Bitmask: spare below threshold, temperature, reliability degraded, read-only mode, volatile memory backup",
        "Битовая маска: резерв, температура, надёжность, режим read-only, энергонезависимая память",
        is_critical=True,
    ),
    "temperature_celsius": _f(
        "Temperature", "Температура",
        "Current NVMe controller temperature in Celsius",
        "Текущая температура контроллера NVMe в градусах Цельсия",
        "°C",
    ),
    "available_spare": _f(
        "Available Spare", "Доступный резерв",
        "Percentage of remaining spare blocks for replacing worn-out ones",
        "Процент оставшихся резервных блоков для замены изношенных",
        "%", is_critical=True,
    ),
    "available_spare_threshold": _f(
        "Available Spare Threshold", "Порог резерва",
        "When Available Spare falls below this value, controller issues a warning",
        "При падении Available Spare ниже этого значения контроллер выдаст предупреждение",
        "%",
    ),
    "percentage_used": _f(
        "Percentage Used", "Износ",
        "Estimated drive resource usage in percent (can exceed 100%)",
        "Оценка использованного ресурса диска в процентах (может превышать 100%)",
        "%",
    ),
    "data_units_read": _f(
        "Data Read", "Прочитано данных",
        "Total data read volume (1 unit = 512 KB = 1000 × 512 bytes)",
        "Общий объём прочитанных данных (1 unit = 512 КБ = 1000 × 512 байт)",
    ),
    "data_units_written": _f(
        "Data Written", "Записано данных",
        "Total data written volume (1 unit = 512 KB = 1000 × 512 bytes)",
        "Общий объём записанных данных (1 unit = 512 КБ = 1000 × 512 байт)",
    ),
    "host_read_commands": _f(
        "Host Read Commands", "Команды чтения",
        "Number of read commands received by the controller from the host",
        "Количество команд чтения, полученных контроллером от хоста",
    ),
    "host_write_commands": _f(
        "Host Write Commands", "Команды записи",
        "Number of write commands received by the controller from the host",
        "Количество команд записи, полученных контроллером от хоста",
    ),
    "controller_busy_time": _f(
        "Controller Busy Time", "Время занятости",
        "Total time the controller was busy processing I/O commands",
        "Суммарное время, когда контроллер был занят обработкой I/O-команд",
        "min",
    ),
    "power_cycles": _f(
        "Power Cycles", "Циклы питания",
        "Number of power on/off cycles",
        "Количество включений/выключений диска",
    ),
    "power_on_hours": _f(
        "Power-On Hours", "Наработка",
        "Total powered-on time",
        "Общее время работы диска во включённом состоянии",
        "hours",
    ),
    "unsafe_shutdowns": _f(
        "Unsafe Shutdowns", "Аварийные выключения",
        "Number of shutdowns without proper cache flush. Frequent unsafe shutdowns may damage data",
        "Количество выключений без корректного сброса кэша. Частые аварийные выключения могут повредить данные",
    ),
    "media_errors": _f(
        "Media Errors", "Ошибки носителя",
        "Number of unrecoverable data integrity errors. Any value > 0 is concerning",
        "Количество неисправимых ошибок целостности данных. Любое значение > 0 — повод для беспокойства",
        is_critical=True,
    ),
    "error_log_entries": _f(
        "Error Log Entries", "Записи в журнале ошибок",
        "Total number of entries in the controller error log",
        "Общее количество записей в журнале ошибок контроллера",
    ),
    "warning_temp_time": _f(
        "Warning Temperature Time", "Время перегрева (пред.)",
        "Total time at temperature above warning threshold",
        "Суммарное время работы при температуре выше порога предупреждения",
        "min",
    ),
    "critical_temp_time": _f(
        "Critical Temperature Time", "Время перегрева (крит.)",
        "Total time at critically high temperature. May cause throttling or emergency shutdown",
        "Суммарное время работы при критически высокой температуре. Может привести к троттлингу или аварийному отключению",
        "min", is_critical=True,
    ),
}
