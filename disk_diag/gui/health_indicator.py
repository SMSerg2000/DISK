"""Виджет-индикатор здоровья диска (GOOD / WARNING / CRITICAL)."""

from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ..core.models import HealthStatus, HealthLevel
from ..i18n import tr


_STYLE_MAP = {
    HealthLevel.GOOD: {
        "bg": "#a6e3a1",
        "fg": "#1e1e2e",
        "text": tr("GOOD", "ХОРОШО"),
        "icon": "OK",
    },
    HealthLevel.WARNING: {
        "bg": "#f9e2af",
        "fg": "#1e1e2e",
        "text": tr("WARNING", "ВНИМАНИЕ"),
        "icon": "!!",
    },
    HealthLevel.CRITICAL: {
        "bg": "#f38ba8",
        "fg": "#1e1e2e",
        "text": tr("CRITICAL", "КРИТИЧНО"),
        "icon": "XX",
    },
    HealthLevel.UNKNOWN: {
        "bg": "#585b70",
        "fg": "#cdd6f4",
        "text": tr("UNKNOWN", "НЕ ОПРЕДЕЛЕНО"),
        "icon": "??",
    },
}


class HealthIndicator(QFrame):
    """Большой бейдж с цветовой индикацией здоровья диска."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.setMaximumHeight(220)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel("??")
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_font = QFont("Segoe UI", 28, QFont.Weight.Bold)
        self._icon_label.setFont(icon_font)

        self._status_label = QLabel("UNKNOWN")
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_font = QFont("Segoe UI", 18, QFont.Weight.Bold)
        self._status_label.setFont(status_font)

        self._summary_label = QLabel("")
        self._summary_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._summary_label.setWordWrap(True)
        summary_font = QFont("Segoe UI", 10)
        self._summary_label.setFont(summary_font)

        layout.addWidget(self._icon_label)
        layout.addWidget(self._status_label)
        layout.addWidget(self._summary_label)

        self._apply_style(HealthLevel.UNKNOWN, "")

    def set_status(self, status: HealthStatus):
        """Обновить отображение на основе HealthStatus."""
        extra_lines = []
        if status.health_score >= 0:
            extra_lines.append(f"Health Score: {status.health_score}/100")
        if status.penalties:
            for reason, pts in status.penalties:
                extra_lines.append(f"  -{pts}: {reason}")
        if status.power_on_hours > 0:
            h = status.power_on_hours
            years = h // (365 * 24)
            months = (h % (365 * 24)) // (30 * 24)
            days = (h % (30 * 24)) // 24
            parts = []
            if years > 0:
                parts.append(f"{years} {tr('y', 'г')}")
            if months > 0:
                parts.append(f"{months} {tr('mo', 'мес')}")
            if days > 0 or not parts:
                parts.append(f"{days} {tr('d', 'дн')}")
            extra_lines.append(f"{tr('Uptime', 'Наработка')}: {' '.join(parts)} ({h:,} {tr('hrs', 'ч')})")
        # TBW и прогноз — только если диск не при смерти (score >= 30)
        if status.health_score >= 30:
            if status.tbw_consumed_tb > 0 and status.tbw_rated_tb > 0:
                pct = status.tbw_consumed_tb / status.tbw_rated_tb * 100
                extra_lines.append(f"TBW: {status.tbw_consumed_tb:.1f} / ~{status.tbw_rated_tb:.0f} TB ({pct:.1f}%)")
            if status.tbw_remaining_days > 0:
                years = status.tbw_remaining_days / 365
                lbl = tr("Forecast", "Прогноз")
                if years > 100:
                    extra_lines.append(f"{lbl}: > 100 {tr('years', 'лет')}")
                elif years >= 1:
                    extra_lines.append(f"{lbl}: ~{years:.1f} {tr('years', 'лет')}")
                else:
                    extra_lines.append(f"{lbl}: ~{status.tbw_remaining_days} {tr('days', 'дней')}")
        summary = status.summary
        if extra_lines:
            summary += "\n" + "\n".join(extra_lines)
        self._apply_style(status.level, summary)

    def clear(self):
        """Сбросить в состояние UNKNOWN."""
        self._apply_style(HealthLevel.UNKNOWN, "")

    def _apply_style(self, level: HealthLevel, summary: str):
        style = _STYLE_MAP.get(level, _STYLE_MAP[HealthLevel.UNKNOWN])

        self.setStyleSheet(f"""
            HealthIndicator {{
                background-color: {style['bg']};
                border-radius: 10px;
                padding: 10px;
            }}
        """)

        self._icon_label.setStyleSheet(f"color: {style['fg']}; background: transparent;")
        self._status_label.setStyleSheet(f"color: {style['fg']}; background: transparent;")
        self._summary_label.setStyleSheet(f"color: {style['fg']}; background: transparent;")

        self._icon_label.setText(style["icon"])
        self._status_label.setText(style["text"])
        self._summary_label.setText(summary)
