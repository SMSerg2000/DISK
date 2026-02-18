"""База данных SMART-атрибутов — ID, имена, описания, критичность."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SmartAttributeInfo:
    id: int
    name: str
    description: str
    is_critical: bool
    unit: Optional[str] = None

    @property
    def better_raw(self) -> str:
        """'lower' = меньше raw — лучше, 'higher' = больше — лучше."""
        if self.id in (231, 232):  # SSD Life Left, Endurance Remaining
            return "higher"
        return "lower"


SMART_ATTRIBUTES: dict[int, SmartAttributeInfo] = {
    # === Общие атрибуты ===
    1:   SmartAttributeInfo(1,   "Read Error Rate",               "Частота ошибок чтения с поверхности",                    False),
    2:   SmartAttributeInfo(2,   "Throughput Performance",        "Общая производительность",                                False),
    3:   SmartAttributeInfo(3,   "Spin-Up Time",                  "Среднее время раскрутки шпинделя",                       False, "ms"),
    4:   SmartAttributeInfo(4,   "Start/Stop Count",              "Количество циклов старт/стоп шпинделя",                  False),
    5:   SmartAttributeInfo(5,   "Reallocated Sectors Count",     "Количество переназначенных секторов (bad → spare)",       True),
    7:   SmartAttributeInfo(7,   "Seek Error Rate",               "Частота ошибок позиционирования головок",                False),
    8:   SmartAttributeInfo(8,   "Seek Time Performance",         "Средняя производительность позиционирования",            False),
    9:   SmartAttributeInfo(9,   "Power-On Hours",                "Время работы диска во включённом состоянии",             False, "hours"),
    10:  SmartAttributeInfo(10,  "Spin Retry Count",              "Количество повторных попыток раскрутки",                 True),
    11:  SmartAttributeInfo(11,  "Recalibration Retries",         "Количество повторных калибровок",                        False),
    12:  SmartAttributeInfo(12,  "Power Cycle Count",             "Количество полных циклов включения/выключения",          False),

    # === Kingston SSD-специфичные атрибуты ===
    100: SmartAttributeInfo(100, "Erase/Program Cycles",          "Общее количество циклов стирания/записи (Kingston)",     False),
    148: SmartAttributeInfo(148, "Total NAND Erase Count",        "Общее количество стираний NAND (Kingston SSD)",          False),
    149: SmartAttributeInfo(149, "Max NAND Erase Count",          "Максимальное количество стираний одного блока NAND (Kingston)", False),
    167: SmartAttributeInfo(167, "Average Erase Count",           "Среднее количество стираний на блок (Kingston SSD)",     False),
    168: SmartAttributeInfo(168, "Max Erase Count NAND",          "Максимальное количество стираний NAND-блока (Kingston)", False),
    169: SmartAttributeInfo(169, "Remaining Life %",              "Оставшийся ресурс в процентах (Kingston SSD)",           False, "%"),
    218: SmartAttributeInfo(218, "CRC Error Count",               "Количество ошибок CRC (Kingston SSD)",                   False),

    # === Общие SSD атрибуты ===
    170: SmartAttributeInfo(170, "Available Reserved Space",      "Оставшийся резерв блоков (SSD)",                         True, "%"),
    171: SmartAttributeInfo(171, "Program Fail Count",            "Количество ошибок программирования flash (SSD)",         True),
    172: SmartAttributeInfo(172, "Erase Fail Count",              "Количество ошибок стирания flash (SSD)",                 True),
    173: SmartAttributeInfo(173, "Wear Leveling Count",           "Средний износ ячеек (SSD)",                              False),
    174: SmartAttributeInfo(174, "Unexpected Power Loss",         "Количество неожиданных отключений питания (SSD)",        False),
    175: SmartAttributeInfo(175, "Program Fail Count Chip",       "Ошибки программирования на уровне чипа (SSD)",           True),
    176: SmartAttributeInfo(176, "Erase Fail Count Chip",         "Ошибки стирания на уровне чипа (SSD)",                   True),
    177: SmartAttributeInfo(177, "Wear Leveling Count",           "Счётчик выравнивания износа flash (SSD)",                False),
    180: SmartAttributeInfo(180, "Unused Reserved Block Count",   "Количество неиспользованных резервных блоков",           True),
    181: SmartAttributeInfo(181, "Program Fail Count Total",      "Общее количество ошибок программирования (SSD)",         True),
    182: SmartAttributeInfo(182, "Erase Fail Count Total",        "Общее количество ошибок стирания (SSD)",                 True),
    183: SmartAttributeInfo(183, "Runtime Bad Block",             "Количество bad-блоков, обнаруженных в работе (SSD)",     True),
    184: SmartAttributeInfo(184, "End-to-End Error",              "Ошибки сквозной проверки целостности данных",            True),
    187: SmartAttributeInfo(187, "Reported Uncorrectable Errors", "Количество неисправимых ошибок ECC",                    True),
    188: SmartAttributeInfo(188, "Command Timeout",               "Количество прерванных операций по таймауту",             True),
    189: SmartAttributeInfo(189, "High Fly Writes",               "Количество записей с увеличенной высотой полёта головки",False),
    190: SmartAttributeInfo(190, "Airflow Temperature",           "Температура воздушного потока",                           False, "°C"),
    191: SmartAttributeInfo(191, "G-Sense Error Rate",            "Количество ошибок из-за ударов/вибрации",                False),
    192: SmartAttributeInfo(192, "Power-Off Retract Count",       "Количество аварийных парковок головок",                   False),
    193: SmartAttributeInfo(193, "Load Cycle Count",              "Количество циклов загрузки/выгрузки головок",            False),
    194: SmartAttributeInfo(194, "Temperature",                   "Температура диска",                                      False, "°C"),
    195: SmartAttributeInfo(195, "Hardware ECC Recovered",        "Количество ошибок, исправленных ECC",                    False),
    196: SmartAttributeInfo(196, "Reallocation Event Count",      "Количество операций переназначения (успех + неудачи)",   True),
    197: SmartAttributeInfo(197, "Current Pending Sector Count",  "Количество нестабильных секторов, ожидающих переназначения", True),
    198: SmartAttributeInfo(198, "Offline Uncorrectable",         "Количество неисправимых ошибок при offline-сканировании", True),
    199: SmartAttributeInfo(199, "UDMA CRC Error Count",          "Количество ошибок CRC при передаче по интерфейсу",      False),
    200: SmartAttributeInfo(200, "Multi-Zone Error Rate",         "Частота ошибок записи в несколько зон",                  False),
    201: SmartAttributeInfo(201, "Soft Read Error Rate",          "Количество программных ошибок чтения",                   True),
    202: SmartAttributeInfo(202, "Data Address Mark Errors",      "Ошибки адресных маркеров / % износа (SSD)",             False),

    # === Samsung SSD-специфичные ===
    220: SmartAttributeInfo(220, "Disk Shift",                    "Смещение диска относительно шпинделя",                   False),
    226: SmartAttributeInfo(226, "Load-In Time",                  "Время загрузки головок на поверхность",                  False),
    231: SmartAttributeInfo(231, "SSD Life Left",                 "Оставшийся ресурс SSD",                                 True, "%"),
    232: SmartAttributeInfo(232, "Endurance Remaining",           "Оставшаяся износостойкость (SSD)",                       True, "%"),
    233: SmartAttributeInfo(233, "Media Wearout Indicator",       "Индикатор износа носителя (SSD)",                        False),
    234: SmartAttributeInfo(234, "Thermal Throttle Status",       "Статус термального троттлинга (SSD)",                    False),
    235: SmartAttributeInfo(235, "Good Block Count",              "Количество исправных блоков (SSD)",                      False),
    240: SmartAttributeInfo(240, "Head Flying Hours",             "Время полёта головок над поверхностью",                  False, "hours"),
    241: SmartAttributeInfo(241, "Total LBAs Written",            "Всего записано логических блоков",                       False),
    242: SmartAttributeInfo(242, "Total LBAs Read",               "Всего прочитано логических блоков",                      False),
    243: SmartAttributeInfo(243, "Total NAND Writes",             "Всего записано на NAND (SSD)",                           False),
    244: SmartAttributeInfo(244, "Total NAND Reads",              "Всего прочитано с NAND (SSD)",                           False),
    249: SmartAttributeInfo(249, "NAND Writes (1GiB)",            "Всего записано на NAND, GiB (SSD)",                     False, "GiB"),
    254: SmartAttributeInfo(254, "Free Fall Protection",          "Количество срабатываний датчика свободного падения",     False),
}

# ID атрибутов, характерных только для SSD
SSD_INDICATOR_ATTRS = {
    148, 149, 167, 168, 169, 170, 171, 172, 173, 174, 175, 176, 177,
    180, 181, 182, 231, 232, 233, 234, 235, 243, 244, 249,
}


def get_attribute_name(attr_id: int) -> str:
    info = SMART_ATTRIBUTES.get(attr_id)
    return info.name if info else f"Unknown ({attr_id})"


def is_critical_attribute(attr_id: int) -> bool:
    info = SMART_ATTRIBUTES.get(attr_id)
    return info.is_critical if info else False


def get_attribute_info(attr_id: int) -> SmartAttributeInfo | None:
    return SMART_ATTRIBUTES.get(attr_id)
