"""Оценка состояния здоровья дисков на основе SMART / NVMe данных."""

from .models import (
    SmartAttribute, NvmeHealthInfo, HealthStatus, HealthLevel,
)
from ..data.smart_db import is_critical_attribute
from ..data.vendor_profiles import match_profile, decode_raw
from ..i18n import tr


def _get_attr_raw(attributes: list[SmartAttribute], attr_id: int) -> int:
    """Получить raw_value атрибута по ID, или -1 если не найден."""
    for a in attributes:
        if a.id == attr_id:
            return a.raw_value
    return -1


def _get_attr_raw_low32(attributes: list[SmartAttribute], attr_id: int) -> int:
    """Получить нижние 4 байта raw_value (SandForce и другие контроллеры
    пакуют вспомогательные счётчики в верхние байты)."""
    for a in attributes:
        if a.id == attr_id:
            return a.raw_value & 0xFFFFFFFF
    return -1


def _get_attr_current(attributes: list[SmartAttribute], attr_id: int) -> int:
    """Получить current value атрибута по ID, или -1 если не найден."""
    for a in attributes:
        if a.id == attr_id:
            return a.current
    return -1


def _ata_health_score(attributes: list[SmartAttribute],
                      profile=None) -> tuple[int, list]:
    """Рассчитать Health Score (0-100) с детализацией штрафов."""
    score = 100
    penalties = []

    def penalize(reason: str, points: int):
        nonlocal score
        if points > 0:
            score -= points
            penalties.append((reason, points))

    def decoded(attr_id):
        """Получить decoded raw через vendor profile (или low32 fallback)."""
        raw = _get_attr_raw(attributes, attr_id)
        if raw <= 0:
            return raw
        if profile:
            return decode_raw(profile, attr_id, raw)
        # Fallback: low32 для критических (SandForce совместимость)
        if attr_id in (5, 196, 197, 198, 187):
            return raw & 0xFFFFFFFF
        return raw

    # Reallocated Sectors (ID 5)
    realloc = decoded(5)
    if realloc > 0:
        penalize(tr(f"Reallocated Sectors: {realloc}", f"Переназначенные секторы: {realloc}"),
                 min(40, realloc * 2))

    # Uncorrectable Errors (ID 187, 198)
    uncorr = max(decoded(187), decoded(198))
    if uncorr > 0:
        penalize(tr(f"Uncorrectable Errors: {uncorr}", f"Неисправимые ошибки: {uncorr}"),
                 min(40, uncorr * 5))

    # Program Fail (ID 171)
    prog_fail = _get_attr_raw(attributes, 171)
    if prog_fail > 0:
        penalize(tr(f"Program Fail: {prog_fail}", f"Ошибки программирования: {prog_fail}"),
                 min(30, prog_fail * 3))

    # Erase Fail (ID 172)
    erase_fail = _get_attr_raw(attributes, 172)
    if erase_fail > 0:
        penalize(tr(f"Erase Fail: {erase_fail}", f"Ошибки стирания: {erase_fail}"),
                 min(30, erase_fail * 3))

    # Pending Sectors (ID 197)
    pending = decoded(197)
    if pending > 0:
        penalize(tr(f"Pending Sectors: {pending}", f"Ожидающие секторы: {pending}"),
                 min(20, pending * 4))

    # SSD Life Left (ID 231)
    life_left = _get_attr_current(attributes, 231)
    if life_left >= 0:
        used_pct = 100 - life_left
        pts = 30 if used_pct > 100 else 25 if used_pct > 90 else 15 if used_pct > 80 else 5 if used_pct > 50 else 0
        if pts:
            penalize(tr(f"SSD Wear: {used_pct}%", f"Износ SSD: {used_pct}%"), pts)

    # Wear Leveling (ID 177)
    wear = _get_attr_current(attributes, 177)
    if wear >= 0 and life_left < 0:
        used_pct = 100 - wear
        pts = 25 if used_pct > 90 else 15 if used_pct > 80 else 5 if used_pct > 50 else 0
        if pts:
            penalize(tr(f"Wear Leveling: {used_pct}%", f"Износ ячеек: {used_pct}%"), pts)

    # Temperature (ID 190, 194)
    temp = _get_attr_raw(attributes, 194)
    if temp < 0:
        temp = _get_attr_raw(attributes, 190)
    if temp > 0:
        temp = temp & 0xFF
        pts = 15 if temp > 80 else 10 if temp > 70 else 5 if temp > 60 else 0
        if pts:
            penalize(tr(f"Temperature: {temp}°C", f"Температура: {temp}°C"), pts)

    # CRC Errors (ID 199)
    crc = _get_attr_raw(attributes, 199)
    if crc > 0:
        penalize(tr(f"CRC Errors: {crc}", f"Ошибки CRC: {crc}"), min(10, crc))

    return max(0, score), penalties


def _is_ssd(attributes: list[SmartAttribute]) -> bool:
    """Определить SSD по наличию SSD-специфичных атрибутов."""
    ssd_ids = {170, 171, 172, 173, 174, 175, 176, 177, 180, 231, 232, 233}
    return any(a.id in ssd_ids for a in attributes)


def _ata_tbw(attributes: list[SmartAttribute], capacity_bytes: int = 0, profile=None):
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

    # Прогноз жизни (decoded POH через vendor profile)
    poh_raw = _get_attr_raw(attributes, 9)
    power_on_hours = decode_raw(profile, 9, poh_raw) if poh_raw > 0 else -1
    if consumed_tb > 0 and power_on_hours is not None and power_on_hours > 24:
        power_on_days = power_on_hours / 24.0
        daily_write_tb = consumed_tb / power_on_days
        if daily_write_tb > 0 and rated_tb > 0:
            remaining_tb = max(0, rated_tb - consumed_tb)
            remaining_days = int(remaining_tb / daily_write_tb)

    # WAF = NAND Writes / Host Writes
    waf = -1.0
    if consumed_tb > 0:
        # ID 249: NAND Writes в GiB
        nand_gib = _get_attr_raw(attributes, 249)
        if nand_gib > 0:
            nand_tb = nand_gib / 1024
            waf = round(nand_tb / consumed_tb, 2)
        else:
            # ID 243: Total NAND Writes (vendor-specific units)
            nand_243 = _get_attr_raw(attributes, 243)
            if nand_243 > 0 and host_writes_lba > 0:
                waf = round(nand_243 / host_writes_lba, 2)

    return consumed_tb, rated_tb, remaining_days, daily_write_tb, waf


def assess_ata_health(attributes: list[SmartAttribute],
                      capacity_bytes: int = 0,
                      model: str = "", firmware: str = "") -> HealthStatus:
    """Оценить здоровье ATA/SATA диска по SMART-атрибутам.

    Health Score (0-100) по формуле SSD_TESTING_SPEC.
    TBW расчёт для SSD (атрибуты 241, 233).
    Vendor profile для корректного декодирования packed raw.
    """
    # Vendor profile
    _profile = match_profile(model, firmware)
    warnings = []
    critical_issues = []

    # Нет атрибутов — SMART не прочитался
    if not attributes:
        return HealthStatus(
            level=HealthLevel.UNKNOWN,
            summary=tr("SMART data not available", "Данные SMART недоступны"),
            health_score=-1,
        )

    # Health Score (с vendor profile для корректного декодирования)
    health_score, score_penalties = _ata_health_score(attributes, _profile)

    # TBW (для SSD)
    consumed_tb, rated_tb, remaining_days, daily_write_tb, waf = \
        _ata_tbw(attributes, capacity_bytes, _profile)

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
        # Но НЕ предупреждать если current >= 100 (максимальное/нормальное значение)
        elif (attr.threshold > 0 and is_critical
              and attr.current < 100
              and attr.current <= attr.threshold + 10):
            warnings.append(
                f"{attr.name} (ID {attr.id}): current={attr.current} "
                f"приближается к threshold={attr.threshold}"
            )

        # Переназначенные/нестабильные/неисправимые секторы — decoded raw > 0
        if attr.id in (5, 196, 197, 198):
            raw_low = decode_raw(_profile, attr.id, attr.raw_value)
            if raw_low > 100:
                critical_issues.append(
                    f"{attr.name} (ID {attr.id}): raw={raw_low} — критическое количество!"
                )
            elif raw_low > 0:
                warnings.append(
                    f"{attr.name} (ID {attr.id}): raw={raw_low} — обнаружены проблемные секторы"
                )

        # Температура > 55°C (decoded через profile)
        if attr.id in (190, 194):
            temp = decode_raw(_profile, attr.id, attr.raw_value)
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

    # Power-On Hours (ID 9) — decoded через vendor profile
    poh_raw = _get_attr_raw(attributes, 9)
    poh = -1
    if poh_raw > 0:
        poh = decode_raw(_profile, 9, poh_raw)

    # Уровень по score + issues
    if critical_issues:
        level = HealthLevel.CRITICAL
        summary = f"CRITICAL (Score: {health_score}/100) — {tr('serious problems found!', 'серьёзные проблемы!')}"
    elif warnings:
        level = HealthLevel.WARNING
        summary = f"WARNING (Score: {health_score}/100) — {tr('issues found', 'есть замечания')}"
    else:
        level = HealthLevel.GOOD
        summary = f"GOOD (Score: {health_score}/100) — {tr('no problems found', 'проблем не обнаружено')}"

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
        power_on_hours=poh,
        penalties=score_penalties,
    )


def _nvme_health_score(info: NvmeHealthInfo) -> tuple[int, list]:
    """Рассчитать Health Score (0-100) с детализацией штрафов."""
    score = 100
    penalties = []

    def penalize(reason: str, points: int):
        nonlocal score
        if points > 0:
            score -= points
            penalties.append((reason, points))

    if info.critical_warning != 0:
        penalize(tr(f"Critical Warning: 0x{info.critical_warning:02X}",
                    f"Критическое предупреждение: 0x{info.critical_warning:02X}"), 30)

    if info.media_errors > 0:
        penalize(tr(f"Media Errors: {info.media_errors}",
                    f"Ошибки носителя: {info.media_errors}"), min(40, info.media_errors * 5))

    if info.percentage_used > 100:
        penalize(tr(f"Wear: {info.percentage_used}%", f"Износ: {info.percentage_used}%"), 30)
    elif info.percentage_used > 90:
        penalize(tr(f"Wear: {info.percentage_used}%", f"Износ: {info.percentage_used}%"), 25)
    elif info.percentage_used > 80:
        penalize(tr(f"Wear: {info.percentage_used}%", f"Износ: {info.percentage_used}%"), 15)
    elif info.percentage_used > 50:
        penalize(tr(f"Wear: {info.percentage_used}%", f"Износ: {info.percentage_used}%"), 5)

    if not info.wmi_fallback and info.available_spare < info.available_spare_threshold:
        penalize(tr(f"Available Spare: {info.available_spare}%",
                    f"Доступный резерв: {info.available_spare}%"), 20)
    elif not info.wmi_fallback and info.available_spare < info.available_spare_threshold + 10:
        penalize(tr(f"Available Spare: {info.available_spare}%",
                    f"Доступный резерв: {info.available_spare}%"), 10)

    t = info.temperature_celsius
    pts = 15 if t > 80 else 10 if t > 70 else 5 if t > 60 else 0
    if pts:
        penalize(tr(f"Temperature: {t}°C", f"Температура: {t}°C"), pts)

    if info.unsafe_shutdowns > 1000:
        penalize(tr(f"Unsafe Shutdowns: {info.unsafe_shutdowns}",
                    f"Аварийные выключения: {info.unsafe_shutdowns}"), 10)
    elif info.unsafe_shutdowns > 100:
        penalize(tr(f"Unsafe Shutdowns: {info.unsafe_shutdowns}",
                    f"Аварийные выключения: {info.unsafe_shutdowns}"), 5)

    if info.critical_temp_time > 0:
        penalize(tr(f"Critical Temp Time: {info.critical_temp_time} min",
                    f"Время крит. перегрева: {info.critical_temp_time} мин"), 10)

    return max(0, score), penalties


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
    health_score, score_penalties = _nvme_health_score(info)

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
        power_on_hours=info.power_on_hours if info.power_on_hours > 0 else -1,
        penalties=score_penalties,
    )
