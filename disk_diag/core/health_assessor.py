"""Оценка состояния здоровья дисков на основе SMART / NVMe данных."""

from .models import (
    SmartAttribute, NvmeHealthInfo, HealthStatus, HealthLevel,
)
from ..data.smart_db import is_critical_attribute


def assess_ata_health(attributes: list[SmartAttribute]) -> HealthStatus:
    """Оценить здоровье ATA/SATA диска по SMART-атрибутам.

    Правила:
    - CRITICAL: критический атрибут current <= threshold,
                или reallocated/pending/uncorrectable raw > 0 и current <= threshold
    - WARNING:  критический атрибут с raw > 0 (для ID 5, 196, 197, 198),
                или current близко к threshold (разница < 10),
                или температура > 55°C
    - GOOD:     всё в порядке
    """
    warnings = []
    critical_issues = []

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
            temp = attr.raw_value & 0xFF  # Температура обычно в младшем байте raw
            if temp > 60:
                critical_issues.append(f"Температура: {temp}°C — критический перегрев!")
            elif temp > 55:
                warnings.append(f"Температура: {temp}°C — повышенная")

    # Определяем итоговый уровень
    if critical_issues:
        level = HealthLevel.CRITICAL
        summary = "CRITICAL — обнаружены серьёзные проблемы!"
    elif warnings:
        level = HealthLevel.WARNING
        summary = "WARNING — есть замечания, требуется внимание"
    else:
        level = HealthLevel.GOOD
        summary = "GOOD — проблем не обнаружено"

    return HealthStatus(
        level=level,
        summary=summary,
        warnings=warnings,
        critical_issues=critical_issues,
    )


def assess_nvme_health(info: NvmeHealthInfo) -> HealthStatus:
    """Оценить здоровье NVMe диска по Health Info Log.

    Правила:
    - CRITICAL: CriticalWarning != 0, spare < threshold, media errors > 0,
                percentage used > 100, температура > 70°C
    - WARNING:  spare близко к threshold, percentage used > 80,
                температура > 60°C, unsafe shutdowns > 100
    - GOOD:     всё в порядке
    """
    warnings = []
    critical_issues = []

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
            f"диск был в состоянии критического перегрева"
        )

    # Unsafe Shutdowns
    if info.unsafe_shutdowns > 100:
        warnings.append(
            f"Unsafe Shutdowns: {info.unsafe_shutdowns} — много аварийных выключений"
        )

    # Итог
    if critical_issues:
        level = HealthLevel.CRITICAL
        summary = "CRITICAL — обнаружены серьёзные проблемы!"
    elif warnings:
        level = HealthLevel.WARNING
        summary = "WARNING — есть замечания, требуется внимание"
    else:
        level = HealthLevel.GOOD
        summary = "GOOD — проблем не обнаружено"

    return HealthStatus(
        level=level,
        summary=summary,
        warnings=warnings,
        critical_issues=critical_issues,
    )
