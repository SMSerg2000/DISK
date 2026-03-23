"""Оценка состояния здоровья дисков на основе SMART / NVMe данных."""

from .models import (
    SmartAttribute, NvmeHealthInfo, HealthStatus, HealthLevel,
)
from ..data.smart_db import is_critical_attribute
from ..i18n import tr


def _get_attr_raw(attributes: list[SmartAttribute], attr_id: int) -> int:
    """Получить raw_value атрибута по ID, или -1 если не найден."""
    for a in attributes:
        if a.id == attr_id:
            return a.raw_value
    return -1


def _get_attr_current(attributes: list[SmartAttribute], attr_id: int) -> int:
    """Получить current value атрибута по ID, или -1 если не найден."""
    for a in attributes:
        if a.id == attr_id:
            return a.current
    return -1


def _ata_health_score(attributes: list[SmartAttribute]) -> int:
    """Рассчитать Health Score (0-100) для ATA/SATA по формуле SSD_TESTING_SPEC."""
    score = 100

    # Reallocated Sectors (ID 5) — до 40 баллов
    realloc = _get_attr_raw(attributes, 5)
    if realloc > 0:
        score -= min(40, realloc * 2)

    # Uncorrectable Errors (ID 187, 198) — до 40 баллов
    uncorr = max(_get_attr_raw(attributes, 187), _get_attr_raw(attributes, 198))
    if uncorr > 0:
        score -= min(40, uncorr * 5)

    # Program Fail (ID 171) — до 30 баллов
    prog_fail = _get_attr_raw(attributes, 171)
    if prog_fail > 0:
        score -= min(30, prog_fail * 3)

    # Erase Fail (ID 172) — до 30 баллов
    erase_fail = _get_attr_raw(attributes, 172)
    if erase_fail > 0:
        score -= min(30, erase_fail * 3)

    # Pending Sectors (ID 197) — до 20 баллов
    pending = _get_attr_raw(attributes, 197)
    if pending > 0:
        score -= min(20, pending * 4)

    # SSD Life Left (ID 231) — current value, 100=новый, 0=мёртвый
    life_left = _get_attr_current(attributes, 231)
    if life_left >= 0:
        used_pct = 100 - life_left
        if used_pct > 100:
            score -= 30
        elif used_pct > 90:
            score -= 25
        elif used_pct > 80:
            score -= 15
        elif used_pct > 50:
            score -= 5

    # Wear Leveling (ID 177) — current value
    wear = _get_attr_current(attributes, 177)
    if wear >= 0 and life_left < 0:  # только если нет ID 231
        used_pct = 100 - wear
        if used_pct > 90:
            score -= 25
        elif used_pct > 80:
            score -= 15
        elif used_pct > 50:
            score -= 5

    # Temperature (ID 190, 194) — до 15 баллов
    temp = _get_attr_raw(attributes, 194)
    if temp < 0:
        temp = _get_attr_raw(attributes, 190)
    if temp > 0:
        temp = temp & 0xFF
        if temp > 80:
            score -= 15
        elif temp > 70:
            score -= 10
        elif temp > 60:
            score -= 5

    # CRC Errors (ID 199) — до 10 баллов
    crc = _get_attr_raw(attributes, 199)
    if crc > 0:
        score -= min(10, crc)

    return max(0, score)


def _is_ssd(attributes: list[SmartAttribute]) -> bool:
    """Определить SSD по наличию SSD-специфичных атрибутов."""
    ssd_ids = {170, 171, 172, 173, 174, 175, 176, 177, 180, 231, 232, 233}
    return any(a.id in ssd_ids for a in attributes)


def _ata_tbw(attributes: list[SmartAttribute], capacity_bytes: int = 0):
    """Рассчитать TBW для ATA SSD. Для HDD возвращает -1.

    Returns: (consumed_tb, rated_tb, remaining_days, daily_write_tb)
    """
    consumed_tb = -1.0
    rated_tb = -1.0
    remaining_days = -1
    daily_write_tb = -1.0

    # TBW имеет смысл только для SSD
    if not _is_ssd(attributes):
        return consumed_tb, rated_tb, remaining_days, daily_write_tb

    # Total Host Writes: ID 241 (в LBA секторах) или ID 233 (vendor-specific)
    host_writes_lba = _get_attr_raw(attributes, 241)
    if host_writes_lba > 0:
        consumed_tb = host_writes_lba * 512 / (1024 ** 4)
    else:
        # ID 233 — у некоторых вендоров в 32MB units
        total_written = _get_attr_raw(attributes, 233)
        if total_written > 0:
            consumed_tb = total_written * 32 / (1024 ** 2)  # 32 MB units → TB

    # Эвристика для rated TBW (TLC consumer)
    if capacity_bytes > 0:
        capacity_tb = capacity_bytes / (1024 ** 4)
        rated_tb = capacity_tb * 600

    # Прогноз жизни
    power_on_hours = _get_attr_raw(attributes, 9)
    if consumed_tb > 0 and power_on_hours is not None and power_on_hours > 24:
        power_on_days = power_on_hours / 24.0
        daily_write_tb = consumed_tb / power_on_days
        if daily_write_tb > 0 and rated_tb > 0:
            remaining_tb = max(0, rated_tb - consumed_tb)
            remaining_days = int(remaining_tb / daily_write_tb)

    return consumed_tb, rated_tb, remaining_days, daily_write_tb


def assess_ata_health(attributes: list[SmartAttribute],
                      capacity_bytes: int = 0) -> HealthStatus:
    """Оценить здоровье ATA/SATA диска по SMART-атрибутам.

    Health Score (0-100) по формуле SSD_TESTING_SPEC.
    TBW расчёт для SSD (атрибуты 241, 233).
    """
    warnings = []
    critical_issues = []

    # Health Score
    health_score = _ata_health_score(attributes)

    # TBW (для SSD)
    consumed_tb, rated_tb, remaining_days, daily_write_tb = \
        _ata_tbw(attributes, capacity_bytes)

    for attr in attributes:
        is_critical = is_critical_attribute(attr.id)

        # Проверка: current <= threshold
        if attr.threshold > 0 and attr.current <= attr.threshold:
            msg = f"{attr.name} (ID {attr.id}): current={attr.current} <= threshold={attr.threshold}"
            if is_critical:
                critical_issues.append(msg)
            else:
                warnings.append(msg)

        # Проверка: критический атрибут current близко к threshold
        elif (attr.threshold > 0 and is_critical
              and attr.current <= attr.threshold + 10):
            warnings.append(
                f"{attr.name} (ID {attr.id}): current={attr.current} "
                f"приближается к threshold={attr.threshold}"
            )

        # Переназначенные/нестабильные/неисправимые секторы — raw > 0
        if attr.id in (5, 196, 197, 198) and attr.raw_value > 0:
            if attr.raw_value > 100:
                critical_issues.append(
                    f"{attr.name} (ID {attr.id}): raw={attr.raw_value} — критическое количество!"
                )
            else:
                warnings.append(
                    f"{attr.name} (ID {attr.id}): raw={attr.raw_value} — обнаружены проблемные секторы"
                )

        # Температура > 55°C
        if attr.id in (190, 194):
            temp = attr.raw_value & 0xFF
            if temp > 60:
                critical_issues.append(f"Температура: {temp}°C — критический перегрев!")
            elif temp > 55:
                warnings.append(f"Температура: {temp}°C — повышенная")

    # TBW прогноз в warnings
    if consumed_tb > 0 and rated_tb > 0:
        pct = consumed_tb / rated_tb * 100
        if pct > 90:
            critical_issues.append(f"TBW: {pct:.0f}% ресурса записи израсходовано!")
        elif pct > 70:
            warnings.append(f"TBW: {pct:.0f}% ресурса записи израсходовано")

    # Уровень по score + issues
    if critical_issues:
        level = HealthLevel.CRITICAL
        summary = f"CRITICAL (Score: {health_score}/100) — {tr("serious problems found!", "серьёзные проблемы!")}"
    elif warnings:
        level = HealthLevel.WARNING
        summary = f"WARNING (Score: {health_score}/100) — {tr("issues found", "есть замечания")}"
    else:
        level = HealthLevel.GOOD
        summary = f"GOOD (Score: {health_score}/100) — {tr("no problems found", "проблем не обнаружено")}"

    return HealthStatus(
        level=level,
        summary=summary,
        warnings=warnings,
        critical_issues=critical_issues,
        health_score=health_score,
        tbw_consumed_tb=consumed_tb,
        tbw_rated_tb=rated_tb,
        tbw_remaining_days=remaining_days,
        daily_write_tb=daily_write_tb,
    )


def _nvme_health_score(info: NvmeHealthInfo) -> int:
    """Рассчитать Health Score (0-100) для NVMe по формуле из SSD_TESTING_SPEC."""
    score = 100

    # Critical Warning (до 30 баллов)
    if info.critical_warning != 0:
        score -= 30

    # Media Errors (до 40 баллов)
    if info.media_errors > 0:
        score -= min(40, info.media_errors * 5)

    # Percentage Used (до 30 баллов)
    if info.percentage_used > 100:
        score -= 30
    elif info.percentage_used > 90:
        score -= 25
    elif info.percentage_used > 80:
        score -= 15
    elif info.percentage_used > 50:
        score -= 5

    # Available Spare (до 20 баллов)
    if not info.wmi_fallback and info.available_spare < info.available_spare_threshold:
        score -= 20
    elif not info.wmi_fallback and info.available_spare < info.available_spare_threshold + 10:
        score -= 10

    # Temperature (до 15 баллов)
    if info.temperature_celsius > 80:
        score -= 15
    elif info.temperature_celsius > 70:
        score -= 10
    elif info.temperature_celsius > 60:
        score -= 5

    # Unsafe Shutdowns (до 10 баллов)
    if info.unsafe_shutdowns > 1000:
        score -= 10
    elif info.unsafe_shutdowns > 100:
        score -= 5

    # Critical Temp Time (до 10 баллов)
    if info.critical_temp_time > 0:
        score -= 10

    return max(0, score)


def _nvme_tbw_and_waf(info: NvmeHealthInfo, capacity_bytes: int = 0):
    """Рассчитать TBW consumed, прогноз жизни и WAF для NVMe.

    Returns:
        (consumed_tb, rated_tb, remaining_days, daily_write_tb, waf)
        Значение -1 = не удалось рассчитать.
    """
    consumed_tb = -1.0
    rated_tb = -1.0
    remaining_days = -1
    daily_write_tb = -1.0
    waf = -1.0

    if info.wmi_fallback:
        return consumed_tb, rated_tb, remaining_days, daily_write_tb, waf

    # Data Units Written → TB (1 unit = 512 KB = 512000 bytes)
    if info.data_units_written > 0:
        consumed_tb = info.data_units_written * 512000 / (1024 ** 4)

    # Эвристика для rated TBW по ёмкости (TLC consumer)
    if capacity_bytes > 0:
        capacity_tb = capacity_bytes / (1024 ** 4)
        rated_tb = capacity_tb * 600  # ~600 TBW на 1 TB для TLC

    # Прогноз жизни
    if consumed_tb > 0 and info.power_on_hours > 24:
        power_on_days = info.power_on_hours / 24.0
        daily_write_tb = consumed_tb / power_on_days
        if daily_write_tb > 0 and rated_tb > 0:
            remaining_tb = max(0, rated_tb - consumed_tb)
            remaining_days = int(remaining_tb / daily_write_tb)

    # WAF — нужны данные о NAND writes (NVMe стандарт не включает их,
    # но percentage_used даёт косвенную оценку)
    # WAF рассчитывается только если есть vendor-specific данные

    return consumed_tb, rated_tb, remaining_days, daily_write_tb, waf


def assess_nvme_health(info: NvmeHealthInfo, capacity_bytes: int = 0) -> HealthStatus:
    """Оценить здоровье NVMe диска по Health Info Log.

    Health Score (0-100) по формуле SSD_TESTING_SPEC.
    TBW расчёт + прогноз оставшегося времени жизни.
    """
    warnings = []
    critical_issues = []

    # Health Score
    health_score = _nvme_health_score(info)

    # TBW и WAF
    consumed_tb, rated_tb, remaining_days, daily_write_tb, waf = \
        _nvme_tbw_and_waf(info, capacity_bytes)

    # --- Диагностические правила ---

    # Critical Warning bits
    if info.critical_warning != 0:
        bits = []
        if info.critical_warning & 0x01:
            bits.append("spare ниже порога")
        if info.critical_warning & 0x02:
            bits.append("температура превышена")
        if info.critical_warning & 0x04:
            bits.append("надёжность снижена")
        if info.critical_warning & 0x08:
            bits.append("read-only режим")
        if info.critical_warning & 0x10:
            bits.append("volatile memory backup failure")
        critical_issues.append(
            f"Critical Warning: 0x{info.critical_warning:02X} ({', '.join(bits)})"
        )

    # Available Spare
    if not info.wmi_fallback:
        if info.available_spare < info.available_spare_threshold:
            critical_issues.append(
                f"Available Spare: {info.available_spare}% < порог {info.available_spare_threshold}%"
            )
        elif info.available_spare < info.available_spare_threshold + 10:
            warnings.append(
                f"Available Spare: {info.available_spare}% — приближается к порогу "
                f"{info.available_spare_threshold}%"
            )

    # Percentage Used
    if info.percentage_used > 100:
        critical_issues.append(
            f"Percentage Used: {info.percentage_used}% — ресурс исчерпан!"
        )
    elif info.percentage_used > 80:
        warnings.append(
            f"Percentage Used: {info.percentage_used}% — значительный износ"
        )

    # Media Errors
    if info.media_errors > 0:
        critical_issues.append(
            f"Media Errors: {info.media_errors} — ошибки целостности данных!"
        )

    # Temperature
    if info.temperature_celsius > 70:
        critical_issues.append(
            f"Температура: {info.temperature_celsius}°C — критический перегрев!"
        )
    elif info.temperature_celsius > 60:
        warnings.append(
            f"Температура: {info.temperature_celsius}°C — повышенная"
        )

    # Critical temperature time
    if info.critical_temp_time > 0:
        critical_issues.append(
            f"Critical Temperature Time: {info.critical_temp_time} мин — "
            f"был критический перегрев"
        )

    # Unsafe Shutdowns
    if info.unsafe_shutdowns > 100:
        warnings.append(
            f"Unsafe Shutdowns: {info.unsafe_shutdowns} — много аварийных выключений"
        )

    # TBW прогноз в warnings
    if consumed_tb > 0 and rated_tb > 0:
        pct = consumed_tb / rated_tb * 100
        if pct > 90:
            critical_issues.append(f"TBW: {pct:.0f}% ресурса записи израсходовано!")
        elif pct > 70:
            warnings.append(f"TBW: {pct:.0f}% ресурса записи израсходовано")

    # Уровень по Health Score
    if critical_issues:
        level = HealthLevel.CRITICAL
        summary = f"CRITICAL (Score: {health_score}/100) — {tr("serious problems found!", "серьёзные проблемы!")}"
    elif warnings:
        level = HealthLevel.WARNING
        summary = f"WARNING (Score: {health_score}/100) — {tr("issues found", "есть замечания")}"
    else:
        level = HealthLevel.GOOD
        summary = f"GOOD (Score: {health_score}/100) — {tr("no problems found", "проблем не обнаружено")}"

    return HealthStatus(
        level=level,
        summary=summary,
        warnings=warnings,
        critical_issues=critical_issues,
        health_score=health_score,
        tbw_consumed_tb=consumed_tb,
        tbw_rated_tb=rated_tb,
        tbw_remaining_days=remaining_days,
        daily_write_tb=daily_write_tb,
        waf=waf,
    )
