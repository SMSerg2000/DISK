"""Панель «Журнал ошибок»: ATA SMART error log / NVMe error info log.

Read-only: читает из диска журнал последних ошибок (что уже ломалось) и
показывает таблицей. Без фоновых операций — один короткий IOCTL по выбору диска
или по кнопке «Обновить».
"""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush

from ..core.error_log import ErrorLogEngine
from ..core.models import ErrorLog, InterfaceType
from ..i18n import tr

logger = logging.getLogger(__name__)

_ERR_BG = QColor(95, 60, 70)   # тёмно-красный фон строки-ошибки


class ErrorLogPanel(QWidget):
    """Панель просмотра журнала ошибок диска."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drive_number: int | None = None
        self._interface: str = ""
        self._model: str = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._btn_reload = QPushButton(tr("🔄  Read error log", "🔄  Прочитать журнал ошибок"))
        self._btn_reload.setFixedHeight(36)
        self._btn_reload.setEnabled(False)
        self._btn_reload.clicked.connect(self._load)

        self._status = QLabel(tr("Select a drive", "Выберите диск"))
        self._status.setStyleSheet("color: #a6adc8;")

        controls.addWidget(self._btn_reload)
        controls.addWidget(self._status, stretch=1)
        layout.addLayout(controls)

        self._info = QLabel(tr(
            "The drive's internal error log — recent command failures the drive recorded "
            "(uncorrectable reads, aborts, interface CRC, etc.). Read-only.",
            "Внутренний журнал ошибок диска — последние сбои команд, которые диск записал "
            "(нечитаемые секторы, aborts, CRC интерфейса и т.д.). Только чтение."))
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color: #6c7086; font-size: 11px;")
        layout.addWidget(self._info)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            "#",
            tr("Power-on hours", "Наработка, ч"),
            tr("Error type", "Тип ошибки"),
            tr("LBA", "LBA"),
            tr("Details", "Детали"),
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, stretch=1)

    # --- Public API (контракт панели) ---

    def set_drive(self, drive_number: int, interface_type: str = "", model: str = ""):
        """Установить диск и сразу прочитать журнал (read-only, безопасно)."""
        self._drive_number = drive_number
        self._interface = interface_type
        self._model = model
        self._table.setRowCount(0)

        if interface_type == InterfaceType.VIRTUAL.value:
            self._btn_reload.setEnabled(False)
            self._set_status(tr("Not available for virtual disks",
                                "Недоступно для виртуальных дисков"), "#6c7086")
            return

        self._btn_reload.setEnabled(True)
        self._load()

    def clear(self):
        """Полный сброс панели."""
        self._drive_number = None
        self._btn_reload.setEnabled(False)
        self._table.setRowCount(0)
        self._set_status(tr("Select a drive", "Выберите диск"), "#a6adc8")

    def is_running(self) -> bool:
        return False  # нет фоновых операций

    def stop(self):
        pass  # нечего останавливать

    # --- Private ---

    def _set_status(self, text: str, color: str):
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _load(self):
        if self._drive_number is None:
            return
        try:
            log = ErrorLogEngine(self._drive_number, self._interface).read()
        except Exception as e:
            logger.debug(f"Error log read failed: {e}")
            self._set_status(tr("Log unavailable", "Журнал недоступен"), "#f9e2af")
            self._table.setRowCount(0)
            return
        self._fill(log)

    def _fill(self, log: ErrorLog):
        self._table.setRowCount(0)
        if not log.supported:
            self._set_status(log.note or tr("Not supported", "Не поддерживается"), "#f9e2af")
            return

        entries = log.entries
        # ATA total_count = накопительный device error count (за время работы);
        # NVMe total_count = лишь число записей в журнале (не пожизненный счётчик),
        # поэтому лейблы честно различаются.
        is_nvme = self._interface == InterfaceType.NVME.value
        if not entries:
            if not is_nvme and log.total_count > 0:
                msg = tr(f"No recent errors in log ✓ (lifetime error count: {log.total_count})",
                         f"Свежих ошибок в журнале нет ✓ (всего за время работы: {log.total_count})")
            else:
                msg = tr("No errors logged ✓", "Ошибок в журнале нет ✓")
            self._set_status(msg, "#a6e3a1")
            return

        if is_nvme:
            msg = tr(f"{len(entries)} error log entries",
                     f"Записей в журнале ошибок: {len(entries)}")
        else:
            msg = tr(f"{len(entries)} shown — lifetime error count: {log.total_count}",
                     f"Показано: {len(entries)} — всего за время работы: {log.total_count}")
        self._set_status(msg, "#f38ba8")

        self._table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            poh = str(e.lifetime_hours) if e.lifetime_hours >= 0 else "—"
            lba = str(e.lba) if e.lba >= 0 else "—"
            cells = [str(e.number), poh, e.description, lba, e.detail]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 1, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setBackground(QBrush(_ERR_BG))
                self._table.setItem(row, col, item)

        header = self._table.horizontalHeader()
        for col in (0, 1, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
