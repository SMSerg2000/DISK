"""Виджет-индикатор здоровья диска (GOOD / WARNING / CRITICAL)."""

from PySide6.QtWidgets import QFrame, QVBoxLayout, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ..core.models import HealthStatus, HealthLevel


_STYLE_MAP = {
    HealthLevel.GOOD: {
        "bg": "#a6e3a1",
        "fg": "#1e1e2e",
        "text": "GOOD",
        "icon": "OK",
    },
    HealthLevel.WARNING: {
        "bg": "#f9e2af",
        "fg": "#1e1e2e",
        "text": "WARNING",
        "icon": "!!",
    },
    HealthLevel.CRITICAL: {
        "bg": "#f38ba8",
        "fg": "#1e1e2e",
        "text": "CRITICAL",
        "icon": "XX",
    },
    HealthLevel.UNKNOWN: {
        "bg": "#585b70",
        "fg": "#cdd6f4",
        "text": "UNKNOWN",
        "icon": "??",
    },
}


class HealthIndicator(QFrame):
    """Большой бейдж с цветовой индикацией здоровья диска."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setMaximumHeight(160)

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
        self._apply_style(status.level, status.summary)

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
