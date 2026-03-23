from ..i18n import tr
"""Панель с базовой информацией о диске."""

from PySide6.QtWidgets import QGroupBox, QGridLayout, QLabel
from PySide6.QtGui import QFont

from ..core.models import DriveInfo
from ..utils.formatting import format_capacity, format_hours


class InfoPanel(QGroupBox):
    """Панель: модель, серийник, прошивка, ёмкость, интерфейс, тип, температура."""

    def __init__(self, parent=None):
        super().__init__(tr("Drive Information", "Информация о диске"), parent)

        layout = QGridLayout(self)
        layout.setSpacing(6)

        self._fields: dict[str, tuple[QLabel, QLabel]] = {}
        field_names = [
            ("model", tr("Model", "Модель")),
            ("serial", tr("Serial Number", "Серийный номер")),
            ("firmware", tr("Firmware", "Прошивка")),
            ("capacity", tr("Capacity", "Ёмкость")),
            ("interface", tr("Interface", "Интерфейс")),
            ("type", tr("Type", "Тип")),
            ("temperature", tr("Temperature", "Температура")),
            ("smart", "SMART"),
        ]

        label_font = QFont("Segoe UI", 11)
        value_font = QFont("Segoe UI", 11, QFont.Weight.Bold)

        for row, (key, label_text) in enumerate(field_names):
            label = QLabel(f"{label_text}:")
            label.setFont(label_font)
            label.setStyleSheet("color: #a6adc8;")

            value = QLabel("—")
            value.setFont(value_font)
            value.setStyleSheet("color: #cdd6f4;")
            value.setTextInteractionFlags(
                value.textInteractionFlags()
                | value.textInteractionFlags().TextSelectableByMouse
            )

            layout.addWidget(label, row, 0)
            layout.addWidget(value, row, 1)
            self._fields[key] = (label, value)

        layout.setColumnStretch(1, 1)

    def set_drive_info(self, info: DriveInfo, temperature: int | None = None):
        """Заполнить панель данными о диске."""
        self._fields["model"][1].setText(info.model.strip())
        self._fields["serial"][1].setText(info.serial_number or "Н/Д")
        self._fields["firmware"][1].setText(info.firmware_revision or "Н/Д")
        self._fields["capacity"][1].setText(format_capacity(info.capacity_bytes))
        self._fields["interface"][1].setText(info.interface_type.value)
        self._fields["type"][1].setText(info.drive_type.value)

        # Температура
        if temperature is not None:
            temp_text = f"{temperature} °C"
            if temperature > 60:
                self._fields["temperature"][1].setStyleSheet(
                    "color: #f38ba8; font-weight: bold;"  # red
                )
            elif temperature > 50:
                self._fields["temperature"][1].setStyleSheet(
                    "color: #f9e2af; font-weight: bold;"  # yellow
                )
            else:
                self._fields["temperature"][1].setStyleSheet(
                    "color: #a6e3a1; font-weight: bold;"  # green
                )
            self._fields["temperature"][1].setText(temp_text)
        else:
            self._fields["temperature"][1].setText("—")
            self._fields["temperature"][1].setStyleSheet("color: #cdd6f4;")

        # SMART
        if info.smart_supported:
            smart_text = tr("Supported, Enabled", "Поддерживается") if info.smart_enabled else tr("Supported, Disabled", "Поддерживается, отключён")
            self._fields["smart"][1].setStyleSheet("color: #a6e3a1; font-weight: bold;")
        else:
            smart_text = tr("Not Supported", "Не поддерживается")
            self._fields["smart"][1].setStyleSheet("color: #585b70; font-weight: bold;")
        self._fields["smart"][1].setText(smart_text)

    def clear(self):
        """Очистить все поля."""
        for key, (label, value) in self._fields.items():
            value.setText("—")
            value.setStyleSheet("color: #cdd6f4;")
