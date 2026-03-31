"""Таблица SMART-атрибутов с цветовой кодировкой."""

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QBrush

from ..core.models import SmartAttribute, NvmeHealthInfo, HealthLevel, HealthStatus
from ..utils.formatting import format_capacity, format_hours, format_smart_raw
from ..data.nvme_fields import NVME_HEALTH_FIELDS
from ..data.smart_db import get_attribute_info
from ..i18n import tr

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

    description_changed = Signal(str)  # HTML-описание выбранного атрибута

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSortingEnabled(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)

        self.currentCellChanged.connect(self._on_cell_changed)

    def _on_cell_changed(self, row, col, prev_row, prev_col):
        """При смене выделенной строки — показать описание атрибута."""
        item = self.item(row, 0)
        if item:
            desc = item.data(Qt.ItemDataRole.UserRole)
            if desc:
                self.description_changed.emit(desc)
                return
        self.description_changed.emit("")

    def set_ata_attributes(self, attributes: list[SmartAttribute],
                          model: str = "", firmware: str = ""):
        """Заполнить таблицу ATA SMART-атрибутами."""
        from ..data.vendor_profiles import match_profile, get_decoded_tooltip
        _vp = match_profile(model, firmware)
        self.setSortingEnabled(False)
        self.clear()

        columns = ["ID", tr("Attribute", "Атрибут"), tr("Current", "Текущ"), tr("Worst", "Худш"), tr("Threshold", "Порог"),
                    tr("Raw Value", "Raw значение"), tr("Status", "Статус")]
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.setRowCount(len(attributes))

        for row, attr in enumerate(attributes):
            # ID
            id_item = QTableWidgetItem()
            id_item.setData(Qt.ItemDataRole.DisplayRole, attr.id)
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Описание для панели (хранится в UserRole первого столбца)
            info = get_attribute_info(attr.id)
            if info:
                desc = f"<b>{info.name}</b> (ID {attr.id})"
                desc += f"<br>{info.description}"
                if info.is_critical:
                    crit_msg = tr("⚠ Critical attribute — affects drive reliability",
                                  "⚠ Критический атрибут — влияет на надёжность диска")
                    desc += f'<br><span style="color: #f9e2af;">{crit_msg}</span>'
            else:
                unk = tr("Unknown Attribute", "Неизвестный атрибут")
                unk_desc = tr("Attribute not found in SMART database",
                              "Атрибут не найден в базе SMART")
                desc = f"<b>{unk}</b> (ID {attr.id})<br>{unk_desc}"
            id_item.setData(Qt.ItemDataRole.UserRole, desc)

            # Name (+ синий цвет для критических)
            name_item = QTableWidgetItem(attr.name)
            if info and info.is_critical:
                name_item.setForeground(QColor(137, 180, 250))  # blue — критический атрибут

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
            # Tooltip с полным сырым значением + разбивка для packed values
            tip = f"Raw: {attr.raw_value} (0x{attr.raw_value:012X})"
            if attr.raw_value > 0xFFFFFF:  # > 16M — вероятно packed (SandForce и др.)
                low16 = attr.raw_value & 0xFFFF
                low32 = attr.raw_value & 0xFFFFFFFF
                tip += f"\nLow16: {low16:,}  |  Low32: {low32:,}"
            dec_tip = get_decoded_tooltip(_vp, attr.id, attr.raw_value)
            if dec_tip:
                tip += f"\n{dec_tip}"
            raw_item.setToolTip(tip)

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

        columns = [tr("Parameter", "Параметр"), tr("Value", "Значение"), tr("Status", "Статус")]
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)

        # Формируем строки для NVMe: (name, value, health, description, is_critical)
        _nf = NVME_HEALTH_FIELDS
        _wmi = info.wmi_fallback  # WMI fallback — показываем только доступные поля

        rows_data = []

        if not _wmi:
            rows_data.append(
                (_nf["critical_warning"].name, f"0x{info.critical_warning:02X}",
                 HealthLevel.CRITICAL if info.critical_warning else HealthLevel.GOOD,
                 _nf["critical_warning"].description, True))

        rows_data.append(
            (_nf["temperature_celsius"].name, f"{info.temperature_celsius} °C",
             HealthLevel.CRITICAL if info.temperature_celsius > 70
             else HealthLevel.WARNING if info.temperature_celsius > 60
             else HealthLevel.GOOD,
             _nf["temperature_celsius"].description, False))

        if not _wmi:
            rows_data.append(
                (_nf["available_spare"].name, f"{info.available_spare}%",
                 HealthLevel.CRITICAL if info.available_spare < info.available_spare_threshold
                 else HealthLevel.WARNING if info.available_spare < info.available_spare_threshold + 10
                 else HealthLevel.GOOD,
                 _nf["available_spare"].description, True))
            rows_data.append(
                (_nf["available_spare_threshold"].name, f"{info.available_spare_threshold}%",
                 HealthLevel.UNKNOWN,
                 _nf["available_spare_threshold"].description, False))

        rows_data.append(
            (_nf["percentage_used"].name, f"{info.percentage_used}%",
             HealthLevel.CRITICAL if info.percentage_used > 100
             else HealthLevel.WARNING if info.percentage_used > 80
             else HealthLevel.GOOD,
             _nf["percentage_used"].description, False))

        if not _wmi:
            rows_data.extend([
                (_nf["data_units_read"].name, format_capacity(info.data_units_read * 512000),
                 HealthLevel.UNKNOWN,
                 _nf["data_units_read"].description, False),
                (_nf["data_units_written"].name, format_capacity(info.data_units_written * 512000),
                 HealthLevel.UNKNOWN,
                 _nf["data_units_written"].description, False),
                (_nf["host_read_commands"].name, f"{info.host_read_commands:,}",
                 HealthLevel.UNKNOWN,
                 _nf["host_read_commands"].description, False),
                (_nf["host_write_commands"].name, f"{info.host_write_commands:,}",
                 HealthLevel.UNKNOWN,
                 _nf["host_write_commands"].description, False),
                (_nf["controller_busy_time"].name,
                 format_hours(info.controller_busy_time // 60) if info.controller_busy_time else "0",
                 HealthLevel.UNKNOWN,
                 _nf["controller_busy_time"].description, False),
            ])

        if info.power_cycles:
            rows_data.append(
                (_nf["power_cycles"].name, f"{info.power_cycles:,}",
                 HealthLevel.UNKNOWN,
                 _nf["power_cycles"].description, False))

        if info.power_on_hours:
            rows_data.append(
                (_nf["power_on_hours"].name, format_hours(info.power_on_hours),
                 HealthLevel.UNKNOWN,
                 _nf["power_on_hours"].description, False))

        if not _wmi:
            rows_data.extend([
                (_nf["unsafe_shutdowns"].name, f"{info.unsafe_shutdowns:,}",
                 HealthLevel.WARNING if info.unsafe_shutdowns > 100 else HealthLevel.GOOD,
                 _nf["unsafe_shutdowns"].description, False),
                (_nf["media_errors"].name, f"{info.media_errors:,}",
                 HealthLevel.CRITICAL if info.media_errors > 0 else HealthLevel.GOOD,
                 _nf["media_errors"].description, True),
                (_nf["error_log_entries"].name, f"{info.error_log_entries:,}",
                 HealthLevel.UNKNOWN,
                 _nf["error_log_entries"].description, False),
            ])

        if _wmi:
            rows_data.append(
                (tr("Data Source", "Источник данных"),
                 tr("WMI (limited — NVMe IOCTL not supported by driver)",
                    "WMI (ограниченные данные — NVMe IOCTL не поддерживается драйвером)"),
                 HealthLevel.UNKNOWN,
                 tr("NVMe IOCTL not supported by driver. Data via WMI (limited).",
                    "NVMe IOCTL не поддерживается драйвером. Данные через WMI (ограниченные)."),
                 False))

        # Добавляем датчики температуры если есть
        for i, temp in enumerate(info.temperature_sensors):
            rows_data.append((
                tr(f"Temperature Sensor {i + 1}", f"Датчик температуры {i + 1}"),
                f"{temp} °C",
                HealthLevel.WARNING if temp > 60 else HealthLevel.GOOD,
                tr(f"Temperature sensor #{i + 1} reading",
                   f"Показание температурного датчика #{i + 1}"),
                False,
            ))

        self.setRowCount(len(rows_data))

        for row, (name, value, health, desc_text, critical) in enumerate(rows_data):
            name_item = QTableWidgetItem(name)
            # Критические параметры — синим
            if critical:
                name_item.setForeground(QColor(137, 180, 250))  # blue
            # Описание для панели
            name_item.setData(Qt.ItemDataRole.UserRole,
                              f"<b>{name}</b><br>{desc_text}")
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

            # Русская подсказка при наведении
            for it in (name_item, value_item, status_item):
                it.setToolTip(desc_text)

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
        self.description_changed.emit("")
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
