"""Таблица SMART-атрибутов с цветовой кодировкой."""

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from ..core.models import SmartAttribute, NvmeHealthInfo, HealthLevel, HealthStatus
from ..utils.formatting import format_capacity, format_hours, format_smart_raw
from ..data.nvme_fields import NVME_HEALTH_FIELDS

# Цвета строк по уровню здоровья
_ROW_COLORS = {
    HealthLevel.GOOD:     QColor(166, 227, 161, 25),   # green, 10% opacity
    HealthLevel.WARNING:  QColor(249, 226, 175, 60),   # yellow
    HealthLevel.CRITICAL: QColor(243, 139, 168, 80),   # red
    HealthLevel.UNKNOWN:  QColor(0, 0, 0, 0),          # transparent
}

_STATUS_TEXT_COLORS = {
    HealthLevel.GOOD:     QColor(166, 227, 161),
    HealthLevel.WARNING:  QColor(249, 226, 175),
    HealthLevel.CRITICAL: QColor(243, 139, 168),
    HealthLevel.UNKNOWN:  QColor(88, 91, 112),
}


class SmartTableWidget(QTableWidget):
    """Таблица SMART-атрибутов для ATA-дисков и NVMe health info."""

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)

    def set_ata_attributes(self, attributes: list[SmartAttribute]):
        """Заполнить таблицу ATA SMART-атрибутами."""
        self.setSortingEnabled(False)
        self.clear()

        columns = ["ID", "Attribute Name", "Current", "Worst", "Threshold",
                    "Raw Value", "Status"]
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.setRowCount(len(attributes))

        for row, attr in enumerate(attributes):
            # ID
            id_item = QTableWidgetItem()
            id_item.setData(Qt.ItemDataRole.DisplayRole, attr.id)
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Name
            name_item = QTableWidgetItem(attr.name)

            # Current
            cur_item = QTableWidgetItem()
            cur_item.setData(Qt.ItemDataRole.DisplayRole, attr.current)
            cur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Worst
            worst_item = QTableWidgetItem()
            worst_item.setData(Qt.ItemDataRole.DisplayRole, attr.worst)
            worst_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Threshold
            thresh_item = QTableWidgetItem()
            thresh_item.setData(Qt.ItemDataRole.DisplayRole, attr.threshold)
            thresh_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Raw Value — форматированное отображение
            raw_display = format_smart_raw(attr.id, attr.raw_value)
            raw_item = QTableWidgetItem(raw_display)
            raw_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # Tooltip с полным сырым значением
            raw_item.setToolTip(f"Raw: {attr.raw_value} (0x{attr.raw_value:012X})")

            # Status
            status_text = attr.health_level.value.upper()
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_color = _STATUS_TEXT_COLORS.get(attr.health_level)
            if status_color:
                status_item.setForeground(QBrush(status_color))

            self.setItem(row, 0, id_item)
            self.setItem(row, 1, name_item)
            self.setItem(row, 2, cur_item)
            self.setItem(row, 3, worst_item)
            self.setItem(row, 4, thresh_item)
            self.setItem(row, 5, raw_item)
            self.setItem(row, 6, status_item)

            # Цвет фона строки
            row_color = _ROW_COLORS.get(attr.health_level, QColor(0, 0, 0, 0))
            for col in range(len(columns)):
                item = self.item(row, col)
                if item:
                    item.setBackground(QBrush(row_color))

        # Настройка ширины колонок
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.setColumnWidth(0, 50)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col in range(2, 7):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        self.setSortingEnabled(True)

    def set_nvme_health(self, info: NvmeHealthInfo, status: HealthStatus):
        """Заполнить таблицу данными NVMe Health Info."""
        self.setSortingEnabled(False)
        self.clear()

        columns = ["Parameter", "Value", "Status"]
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)

        # Формируем строки для NVMe
        rows_data = [
            ("Critical Warning", f"0x{info.critical_warning:02X}",
             HealthLevel.CRITICAL if info.critical_warning else HealthLevel.GOOD),
            ("Temperature", f"{info.temperature_celsius} °C",
             HealthLevel.CRITICAL if info.temperature_celsius > 70
             else HealthLevel.WARNING if info.temperature_celsius > 60
             else HealthLevel.GOOD),
            ("Available Spare", f"{info.available_spare}%",
             HealthLevel.CRITICAL if info.available_spare < info.available_spare_threshold
             else HealthLevel.WARNING if info.available_spare < info.available_spare_threshold + 10
             else HealthLevel.GOOD),
            ("Available Spare Threshold", f"{info.available_spare_threshold}%",
             HealthLevel.UNKNOWN),
            ("Percentage Used", f"{info.percentage_used}%",
             HealthLevel.CRITICAL if info.percentage_used > 100
             else HealthLevel.WARNING if info.percentage_used > 80
             else HealthLevel.GOOD),
            ("Data Read", format_capacity(info.data_units_read * 512000),
             HealthLevel.UNKNOWN),
            ("Data Written", format_capacity(info.data_units_written * 512000),
             HealthLevel.UNKNOWN),
            ("Host Read Commands", f"{info.host_read_commands:,}",
             HealthLevel.UNKNOWN),
            ("Host Write Commands", f"{info.host_write_commands:,}",
             HealthLevel.UNKNOWN),
            ("Controller Busy Time", format_hours(info.controller_busy_time // 60) if info.controller_busy_time else "0",
             HealthLevel.UNKNOWN),
            ("Power Cycles", f"{info.power_cycles:,}",
             HealthLevel.UNKNOWN),
            ("Power-On Hours", format_hours(info.power_on_hours),
             HealthLevel.UNKNOWN),
            ("Unsafe Shutdowns", f"{info.unsafe_shutdowns:,}",
             HealthLevel.WARNING if info.unsafe_shutdowns > 100 else HealthLevel.GOOD),
            ("Media Errors", f"{info.media_errors:,}",
             HealthLevel.CRITICAL if info.media_errors > 0 else HealthLevel.GOOD),
            ("Error Log Entries", f"{info.error_log_entries:,}",
             HealthLevel.UNKNOWN),
        ]

        # Добавляем датчики температуры если есть
        for i, temp in enumerate(info.temperature_sensors):
            rows_data.append((
                f"Temperature Sensor {i + 1}", f"{temp} °C",
                HealthLevel.WARNING if temp > 60 else HealthLevel.GOOD,
            ))

        self.setRowCount(len(rows_data))

        for row, (name, value, health) in enumerate(rows_data):
            name_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem(value)
            value_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            if health == HealthLevel.UNKNOWN:
                status_text = "—"
            else:
                status_text = health.value.upper()
            status_item = QTableWidgetItem(status_text)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            status_color = _STATUS_TEXT_COLORS.get(health)
            if status_color and health != HealthLevel.UNKNOWN:
                status_item.setForeground(QBrush(status_color))

            self.setItem(row, 0, name_item)
            self.setItem(row, 1, value_item)
            self.setItem(row, 2, status_item)

            row_color = _ROW_COLORS.get(health, QColor(0, 0, 0, 0))
            for col in range(3):
                item = self.item(row, col)
                if item:
                    item.setBackground(QBrush(row_color))

        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

        self.setSortingEnabled(True)

    def show_message(self, message: str):
        """Показать сообщение вместо данных (напр. 'SMART not supported')."""
        self.setSortingEnabled(False)
        self.clear()
        self.setColumnCount(1)
        self.setHorizontalHeaderLabels([""])
        self.setRowCount(1)

        item = QTableWidgetItem(message)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(QBrush(QColor(88, 91, 112)))
        self.setItem(0, 0, item)

        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
