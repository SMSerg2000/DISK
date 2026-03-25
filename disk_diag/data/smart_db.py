"""База данных SMART-атрибутов — ID, имена, описания, критичность."""

from dataclasses import dataclass
from typing import Optional
from ..i18n import tr


@dataclass(frozen=True)
class SmartAttributeInfo:
    id: int
    name_en: str
    name_ru: str
    desc_en: str
    desc_ru: str
    is_critical: bool
    unit: Optional[str] = None

    @property
    def name(self) -> str:
        return tr(self.name_en, self.name_ru)

    @property
    def description(self) -> str:
        return tr(self.desc_en, self.desc_ru)

    @property
    def better_raw(self) -> str:
        if self.id in (231, 232):
            return "higher"
        return "lower"


def _a(id, name_en, name_ru, desc_en, desc_ru, critical, unit=None):
    """Короткий конструктор для читаемости."""
    return SmartAttributeInfo(id, name_en, name_ru, desc_en, desc_ru, critical, unit)


SMART_ATTRIBUTES: dict[int, SmartAttributeInfo] = {
    # === Общие атрибуты ===
    1:   _a(1,   "Read Error Rate",               "Ошибки чтения",
               "Frequency of read errors from disk surface",
               "Частота ошибок чтения с поверхности", False),
    2:   _a(2,   "Throughput Performance",        "Производительность",
               "Overall throughput performance",
               "Общая производительность", False),
    3:   _a(3,   "Spin-Up Time",                  "Время раскрутки",
               "Average spin-up time of the spindle motor",
               "Среднее время раскрутки шпинделя", False, "ms"),
    4:   _a(4,   "Start/Stop Count",              "Циклы старт/стоп",
               "Number of spindle start/stop cycles",
               "Количество циклов старт/стоп шпинделя", False),
    5:   _a(5,   "Reallocated Sectors Count",     "Переназначенные секторы",
               "Number of reallocated sectors (bad → spare)",
               "Количество переназначенных секторов (bad → spare)", True),
    7:   _a(7,   "Seek Error Rate",               "Ошибки позиционирования",
               "Frequency of head positioning errors",
               "Частота ошибок позиционирования головок", False),
    8:   _a(8,   "Seek Time Performance",         "Скорость позиционирования",
               "Average seek time performance",
               "Средняя производительность позиционирования", False),
    9:   _a(9,   "Power-On Hours",                "Время работы",
               "Total time the drive has been powered on",
               "Время работы диска во включённом состоянии", False, "hours"),
    10:  _a(10,  "Spin Retry Count",              "Повторные раскрутки",
               "Number of spin-up retry attempts",
               "Количество повторных попыток раскрутки", True),
    11:  _a(11,  "Recalibration Retries",         "Повторные калибровки",
               "Number of recalibration retries",
               "Количество повторных калибровок", False),
    12:  _a(12,  "Power Cycle Count",             "Циклы включения",
               "Number of full power on/off cycles",
               "Количество полных циклов включения/выключения", False),

    13:  _a(13,  "Soft Read Error Rate",          "Ошибки мягкого чтения",
               "Soft read error rate (SandForce controllers)",
               "Частота мягких ошибок чтения (контроллеры SandForce)", False),

    # === Kingston SSD ===
    100: _a(100, "Erase/Program Cycles",          "Циклы стирания/записи",
               "Total erase/program cycles (Kingston)",
               "Общее количество циклов стирания/записи (Kingston)", False),
    148: _a(148, "Total NAND Erase Count",        "Всего стираний NAND",
               "Total NAND erase count (Kingston SSD)",
               "Общее количество стираний NAND (Kingston SSD)", False),
    149: _a(149, "Max NAND Erase Count",          "Макс. стираний NAND",
               "Maximum erase count for a single NAND block (Kingston)",
               "Максимальное количество стираний одного блока NAND (Kingston)", False),
    167: _a(167, "Average Erase Count",           "Среднее стираний",
               "Average erase count per block (Kingston SSD)",
               "Среднее количество стираний на блок (Kingston SSD)", False),
    168: _a(168, "Max Erase Count NAND",          "Макс. стираний блока",
               "Maximum NAND block erase count (Kingston)",
               "Максимальное количество стираний NAND-блока (Kingston)", False),
    169: _a(169, "Remaining Life %",              "Остаток ресурса %",
               "Remaining life percentage (Kingston SSD)",
               "Оставшийся ресурс в процентах (Kingston SSD)", False, "%"),
    218: _a(218, "CRC Error Count",               "Ошибки CRC",
               "CRC error count (Kingston SSD)",
               "Количество ошибок CRC (Kingston SSD)", False),

    # === Silicon Motion / Transcend SSD ===
    160: _a(160, "Uncorrectable Sector Count",    "Неисправимые секторы",
               "Uncorrectable sector count (Silicon Motion SSD)",
               "Количество неисправимых секторов (Silicon Motion SSD)", True),
    161: _a(161, "Valid Spare Blocks",            "Резервные блоки",
               "Available spare blocks (Silicon Motion)",
               "Количество доступных резервных блоков (Silicon Motion)", False),
    163: _a(163, "Initial Invalid Blocks",        "Начальные невалидные блоки",
               "Initial invalid blocks from manufacturing (SM SSD)",
               "Начальные невалидные блоки при производстве (SM SSD)", False),
    164: _a(164, "Total Erase Count",             "Всего стираний",
               "Total erase count for all blocks (SM SSD)",
               "Суммарное количество стираний всех блоков (SM SSD)", False),
    165: _a(165, "Maximum Erase Count",           "Макс. стираний блока",
               "Maximum erase count per block (SM SSD)",
               "Максимальное количество стираний одного блока (SM SSD)", False),
    166: _a(166, "Minimum Erase Count",           "Мин. стираний блока",
               "Minimum erase count per block (SM SSD)",
               "Минимальное количество стираний одного блока (SM SSD)", False),

    # === Общие SSD атрибуты ===
    170: _a(170, "Available Reserved Space",      "Доступный резерв",
               "Remaining reserve block pool (SSD)",
               "Оставшийся резерв блоков (SSD)", True, "%"),
    171: _a(171, "Program Fail Count",            "Ошибки программирования",
               "Flash program fail count (SSD)",
               "Количество ошибок программирования flash (SSD)", True),
    172: _a(172, "Erase Fail Count",              "Ошибки стирания",
               "Flash erase fail count (SSD)",
               "Количество ошибок стирания flash (SSD)", True),
    173: _a(173, "Wear Leveling Count",           "Износ ячеек",
               "Average cell wear level (SSD)",
               "Средний износ ячеек (SSD)", False),
    174: _a(174, "Unexpected Power Loss",         "Аварийные отключения",
               "Unexpected power loss count (SSD)",
               "Количество неожиданных отключений питания (SSD)", False),
    175: _a(175, "Program Fail Count Chip",       "Ошибки прогр. (чип)",
               "Chip-level program fail count (SSD)",
               "Ошибки программирования на уровне чипа (SSD)", True),
    176: _a(176, "Erase Fail Count Chip",         "Ошибки стир. (чип)",
               "Chip-level erase fail count (SSD)",
               "Ошибки стирания на уровне чипа (SSD)", True),
    177: _a(177, "Wear Leveling Count",           "Выравнивание износа",
               "Wear leveling cycle count (SSD)",
               "Счётчик выравнивания износа flash (SSD)", False),
    180: _a(180, "Unused Reserved Block Count",   "Неисп. резервные блоки",
               "Unused reserved block count",
               "Количество неиспользованных резервных блоков", True),
    181: _a(181, "Program Fail Count Total",      "Всего ошибок прогр.",
               "Total program fail count (SSD)",
               "Общее количество ошибок программирования (SSD)", True),
    182: _a(182, "Erase Fail Count Total",        "Всего ошибок стирания",
               "Total erase fail count (SSD)",
               "Общее количество ошибок стирания (SSD)", True),
    183: _a(183, "Runtime Bad Block",             "Bad-блоки в работе",
               "Bad blocks discovered during runtime (SSD)",
               "Количество bad-блоков, обнаруженных в работе (SSD)", True),
    184: _a(184, "End-to-End Error",              "Сквозные ошибки",
               "End-to-end data integrity check errors",
               "Ошибки сквозной проверки целостности данных", True),
    187: _a(187, "Reported Uncorrectable Errors", "Неисправимые ошибки",
               "ECC uncorrectable error count",
               "Количество неисправимых ошибок ECC", True),
    188: _a(188, "Command Timeout",               "Таймауты команд",
               "Timed-out command count",
               "Количество прерванных операций по таймауту", True),
    189: _a(189, "High Fly Writes",               "Высокий полёт головки",
               "Write operations with excessive head height",
               "Количество записей с увеличенной высотой полёта головки", False),
    190: _a(190, "Airflow Temperature",           "Температура воздуха",
               "Airflow temperature",
               "Температура воздушного потока", False, "°C"),
    191: _a(191, "G-Sense Error Rate",            "Ошибки от вибрации",
               "Shock/vibration induced errors",
               "Количество ошибок из-за ударов/вибрации", False),
    192: _a(192, "Power-Off Retract Count",       "Аварийные парковки",
               "Emergency head retract count",
               "Количество аварийных парковок головок", False),
    193: _a(193, "Load Cycle Count",              "Циклы загрузки головок",
               "Head load/unload cycle count",
               "Количество циклов загрузки/выгрузки головок", False),
    194: _a(194, "Temperature",                   "Температура",
               "Drive temperature",
               "Температура диска", False, "°C"),
    195: _a(195, "Hardware ECC Recovered",        "Исправлено ECC",
               "ECC-corrected error count",
               "Количество ошибок, исправленных ECC", False),
    196: _a(196, "Reallocation Event Count",      "События переназначения",
               "Reallocation event count (success + failure)",
               "Количество операций переназначения (успех + неудачи)", True),
    197: _a(197, "Current Pending Sector Count",  "Ожидающие переназначения",
               "Unstable sectors awaiting reallocation",
               "Количество нестабильных секторов, ожидающих переназначения", True),
    198: _a(198, "Offline Uncorrectable",         "Неиспр. офлайн-ошибки",
               "Uncorrectable errors during offline scan",
               "Количество неисправимых ошибок при offline-сканировании", True),
    199: _a(199, "UDMA CRC Error Count",          "Ошибки CRC интерфейса",
               "Interface CRC error count",
               "Количество ошибок CRC при передаче по интерфейсу", False),
    200: _a(200, "Multi-Zone Error Rate",         "Ошибки мультизоны",
               "Multi-zone write error rate",
               "Частота ошибок записи в несколько зон", False),
    201: _a(201, "Soft Read Error Rate",          "Программные ошибки чтения",
               "Software read error count",
               "Количество программных ошибок чтения", True),
    202: _a(202, "Data Address Mark Errors",      "Ошибки адресных маркеров",
               "Data address mark errors / SSD wear %",
               "Ошибки адресных маркеров / % износа (SSD)", False),
    203: _a(203, "Run Out Cancel",                "Отмены ECC-коррекции",
               "ECC correction cancellation count (SM SSD)",
               "Количество отменённых операций ECC-коррекции (SM SSD)", False),

    204: _a(204, "Soft ECC Correction",           "Программная коррекция ECC",
               "Soft ECC correction count (SandForce)",
               "Количество программных коррекций ECC (SandForce)", False),

    # === Samsung SSD-специфичные ===
    230: _a(230, "Drive Life Protection Status", "Статус защиты ресурса",
               "Drive life protection status / head amplitude",
               "Статус защиты ресурса диска / амплитуда головок", False),
    220: _a(220, "Disk Shift",                    "Смещение диска",
               "Disk shift relative to spindle",
               "Смещение диска относительно шпинделя", False),
    226: _a(226, "Load-In Time",                  "Время загрузки головок",
               "Head load-in time",
               "Время загрузки головок на поверхность", False),
    231: _a(231, "SSD Life Left",                 "Остаток ресурса SSD",
               "Remaining SSD life",
               "Оставшийся ресурс SSD", True, "%"),
    232: _a(232, "Endurance Remaining",           "Остаток износостойкости",
               "Remaining endurance (SSD)",
               "Оставшаяся износостойкость (SSD)", True, "%"),
    233: _a(233, "Media Wearout Indicator",       "Индикатор износа",
               "Media wearout indicator (SSD)",
               "Индикатор износа носителя (SSD)", False),
    234: _a(234, "Thermal Throttle Status",       "Термальный троттлинг",
               "Thermal throttle status (SSD)",
               "Статус термального троттлинга (SSD)", False),
    235: _a(235, "Good Block Count",              "Исправные блоки",
               "Good block count (SSD)",
               "Количество исправных блоков (SSD)", False),
    240: _a(240, "Head Flying Hours",             "Время полёта головок",
               "Head flying hours over surface",
               "Время полёта головок над поверхностью", False, "hours"),
    241: _a(241, "Total LBAs Written",            "Всего записано LBA",
               "Total logical blocks written",
               "Всего записано логических блоков", False),
    242: _a(242, "Total LBAs Read",               "Всего прочитано LBA",
               "Total logical blocks read",
               "Всего прочитано логических блоков", False),
    243: _a(243, "Total NAND Writes",             "Всего записано NAND",
               "Total NAND writes (SSD)",
               "Всего записано на NAND (SSD)", False),
    244: _a(244, "Total NAND Reads",              "Всего прочитано NAND",
               "Total NAND reads (SSD)",
               "Всего прочитано с NAND (SSD)", False),
    245: _a(245, "Flash Writes (GB)",             "Записи Flash (ГБ)",
               "NAND flash write volume in GB (SM SSD)",
               "Объём записей на NAND-flash в гигабайтах (SM SSD)", False, "GB"),
    249: _a(249, "NAND Writes (1GiB)",            "Записи NAND (ГиБ)",
               "Total NAND writes in GiB (SSD)",
               "Всего записано на NAND, GiB (SSD)", False, "GiB"),
    250: _a(250, "Read Error Retry Rate",         "Повторы чтения",
               "Read error retry count (SM SSD)",
               "Количество повторных попыток чтения (SM SSD)", False),
    254: _a(254, "Free Fall Protection",          "Защита от падения",
               "Free fall sensor trigger count",
               "Количество срабатываний датчика свободного падения", False),
}

# ID атрибутов, характерных только для SSD
SSD_INDICATOR_ATTRS = {
    148, 149, 167, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177,
    180, 181, 182, 231, 232, 233, 234, 235, 243, 244, 249,
}


def get_attribute_name(attr_id: int) -> str:
    info = SMART_ATTRIBUTES.get(attr_id)
    return info.name if info else tr(f"Unknown ({attr_id})", f"Неизвестный ({attr_id})")


def is_critical_attribute(attr_id: int) -> bool:
    info = SMART_ATTRIBUTES.get(attr_id)
    return info.is_critical if info else False


def get_attribute_info(attr_id: int) -> SmartAttributeInfo | None:
    return SMART_ATTRIBUTES.get(attr_id)
