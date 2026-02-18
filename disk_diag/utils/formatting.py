"""Утилиты форматирования: ёмкость, время, температура."""


def format_capacity(bytes_val: int) -> str:
    """Форматировать ёмкость в человекочитаемый вид.

    Примеры:
        1000204886016 -> "931.5 GB"
        512110190592  -> "476.9 GB"
        2000398934016 -> "1.82 TB"
    """
    if bytes_val <= 0:
        return "N/A"

    units = [("TB", 1024 ** 4), ("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)]
    for unit, divisor in units:
        if bytes_val >= divisor:
            value = bytes_val / divisor
            if value >= 100:
                return f"{value:.1f} {unit}"
            elif value >= 10:
                return f"{value:.2f} {unit}"
            else:
                return f"{value:.2f} {unit}"

    return f"{bytes_val} bytes"


def format_hours(hours: int) -> str:
    """Форматировать часы наработки.

    Примеры:
        123   -> "123 h"
        1234  -> "1,234 h (51d)"
        12345 -> "12,345 h (1y 149d)"
    """
    if hours <= 0:
        return "0 h"

    parts = [f"{hours:,} h"]

    if hours >= 8760:  # > 1 year
        years = hours // 8760
        days = (hours % 8760) // 24
        parts.append(f"({years}y {days}d)")
    elif hours >= 24:
        days = hours // 24
        parts.append(f"({days}d)")

    return " ".join(parts)


def format_temperature(celsius: int) -> str:
    """Форматировать температуру."""
    return f"{celsius} °C"


def format_smart_raw(attr_id: int, raw_value: int) -> str:
    """Форматировать raw value SMART-атрибута с учётом его типа.

    Разные атрибуты хранят данные по-разному:
    - Temperature (190, 194): младший байт = °C, старшие = min/max/lifetime
    - Power-On Hours (9, 240): часы наработки
    - Total LBAs (241, 242): логические блоки (можно перевести в GB)
    """
    # Температура: младший байт = текущая температура
    if attr_id in (190, 194):
        temp = raw_value & 0xFF
        # Некоторые вендоры пакуют min/max в байты 2-5
        byte2 = (raw_value >> 16) & 0xFF
        byte4 = (raw_value >> 32) & 0xFF
        if byte2 > 0 and byte4 > 0 and byte2 != temp and byte4 != temp:
            return f"{temp} °C (min {byte4}, max {byte2})"
        return f"{temp} °C"

    # Часы наработки
    if attr_id in (9, 240):
        return format_hours(raw_value)

    # Power Cycle Count, Start/Stop Count
    if attr_id in (4, 12, 192, 193):
        return f"{raw_value:,}"

    # Total LBAs Written/Read — перевод в TB/GB (512 байт на LBA)
    if attr_id in (241, 242):
        total_bytes = raw_value * 512
        if total_bytes > 0:
            return f"{raw_value:,} ({format_capacity(total_bytes)})"
        return f"{raw_value:,}"

    # NAND Writes in GiB
    if attr_id == 249:
        return f"{raw_value:,} GiB"

    # По умолчанию — число с разделителями тысяч
    if raw_value >= 10000:
        return f"{raw_value:,}"

    return str(raw_value)
