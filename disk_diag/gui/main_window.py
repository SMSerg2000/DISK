"""Главное окно приложения DISK Diagnostic Tool."""

import logging

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QStatusBar, QMenuBar, QMessageBox, QLabel,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject

from .. import __version__, __app_name__
from ..core.models import DriveInfo, DriveType, InterfaceType, HealthLevel
from ..core.drive_enumerator import enumerate_drives
from ..core.smart_ata import read_smart_attributes, detect_drive_type_from_smart, get_temperature_from_smart
from ..core.smart_nvme import read_nvme_health
from ..core.health_assessor import assess_ata_health, assess_nvme_health
from ..core.winapi import DeviceHandle, DiskAccessError
from .drive_selector import DriveSelector
from .info_panel import InfoPanel
from .smart_table import SmartTableWidget
from .health_indicator import HealthIndicator

logger = logging.getLogger(__name__)


class _SmartWorker(QObject):
    """Фоновый воркер для чтения SMART данных (чтобы GUI не зависал)."""
    finished = Signal(object)  # (attributes_or_nvme_health, health_status)
    error = Signal(str)

    def __init__(self, drive_info: DriveInfo):
        super().__init__()
        self.drive_info = drive_info

    def run(self):
        try:
            if self.drive_info.interface_type == InterfaceType.NVME:
                with DeviceHandle(self.drive_info.drive_number) as h:
                    health_info = read_nvme_health(h)
                    status = assess_nvme_health(health_info)
                    self.finished.emit(("nvme", health_info, status))
            elif self.drive_info.smart_supported:
                with DeviceHandle(self.drive_info.drive_number) as h:
                    attrs = read_smart_attributes(h, self.drive_info.drive_number)
                    status = assess_ata_health(attrs)
                    self.finished.emit(("ata", attrs, status))
            else:
                self.finished.emit(("none", None, None))
        except DiskAccessError as e:
            self.error.emit(str(e))
        except Exception as e:
            logger.exception("SMART read error")
            self.error.emit(f"Unexpected error: {e}")


class MainWindow(QMainWindow):
    """Главное окно DISK Diagnostic Tool."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{__app_name__} v{__version__}")
        self.setMinimumSize(900, 650)
        self.resize(1050, 750)

        self._drives: list[DriveInfo] = []
        self._worker_thread: QThread | None = None
        self._worker: _SmartWorker | None = None

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()

        # Загрузка дисков при старте
        self._refresh_drives()

    def _setup_menu(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("File")
        file_menu.addAction("Refresh", self._refresh_drives, "F5")
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close, "Alt+F4")

        help_menu = menu_bar.addMenu("Help")
        help_menu.addAction("About", self._show_about)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(12, 8, 12, 8)

        # Селектор дисков
        self._drive_selector = DriveSelector()
        self._drive_selector.drive_selected.connect(self._on_drive_selected)
        self._drive_selector.refresh_requested.connect(self._refresh_drives)
        main_layout.addWidget(self._drive_selector)

        # Верхняя панель: Info + Health
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        self._info_panel = InfoPanel()
        self._health_indicator = HealthIndicator()

        top_layout.addWidget(self._info_panel, stretch=2)
        top_layout.addWidget(self._health_indicator, stretch=1)
        main_layout.addLayout(top_layout)

        # Таблица SMART
        self._smart_table = SmartTableWidget()
        main_layout.addWidget(self._smart_table, stretch=1)

    def _setup_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_admin = QLabel()
        self._status_drives = QLabel()

        self._statusbar.addPermanentWidget(self._status_admin)
        self._statusbar.addPermanentWidget(self._status_drives)

        from ..utils.admin import is_admin
        if is_admin():
            self._status_admin.setText("Admin: Yes")
            self._status_admin.setStyleSheet("color: #a6e3a1;")
        else:
            self._status_admin.setText("Admin: No")
            self._status_admin.setStyleSheet("color: #f38ba8;")

    def _refresh_drives(self):
        """Перечитать список дисков."""
        self._statusbar.showMessage("Scanning drives...", 3000)
        self._info_panel.clear()
        self._health_indicator.clear()
        self._smart_table.show_message("Scanning...")

        try:
            self._drives = enumerate_drives()
            self._drive_selector.set_drives(self._drives)
            self._status_drives.setText(f"Drives: {len(self._drives)} detected")
            if not self._drives:
                self._smart_table.show_message("No drives detected. Run as Administrator.")
                self._statusbar.showMessage("No drives found", 5000)
            else:
                self._statusbar.showMessage(f"Found {len(self._drives)} drive(s)", 3000)
        except Exception as e:
            logger.exception("Drive enumeration error")
            self._smart_table.show_message(f"Error: {e}")
            self._statusbar.showMessage(f"Error: {e}", 5000)

    def _on_drive_selected(self, index: int):
        """Обработчик выбора диска."""
        if index < 0 or index >= len(self._drives):
            return

        drive = self._drives[index]
        self._info_panel.set_drive_info(drive)
        self._health_indicator.clear()
        self._smart_table.show_message("Reading SMART data...")
        self._statusbar.showMessage(f"Reading SMART data for {drive.model.strip()}...", 10000)

        # Запуск чтения SMART в фоновом потоке
        self._start_smart_read(drive)

    def _start_smart_read(self, drive: DriveInfo):
        """Запустить фоновое чтение SMART."""
        # Остановить предыдущий поток если есть
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(2000)

        self._worker = _SmartWorker(drive)
        self._worker_thread = QThread()
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_smart_finished)
        self._worker.error.connect(self._on_smart_error)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)

        self._worker_thread.start()

    def _on_smart_finished(self, result: tuple):
        """Обработчик завершения чтения SMART."""
        data_type = result[0]

        if data_type == "ata":
            _, attrs, status = result
            self._smart_table.set_ata_attributes(attrs)
            self._health_indicator.set_status(status)

            # Обновляем тип диска и температуру по SMART данным
            drive = self._drive_selector.get_selected_drive()
            if drive and drive.drive_type == DriveType.UNKNOWN:
                drive.drive_type = detect_drive_type_from_smart(attrs)
            temp = get_temperature_from_smart(attrs)
            if drive:
                self._info_panel.set_drive_info(drive, temperature=temp)

            self._statusbar.showMessage(
                f"SMART: {len(attrs)} attributes loaded — {status.summary}", 5000
            )

        elif data_type == "nvme":
            _, health_info, status = result
            self._smart_table.set_nvme_health(health_info, status)
            self._health_indicator.set_status(status)

            # Обновляем температуру из NVMe
            drive = self._drive_selector.get_selected_drive()
            if drive:
                self._info_panel.set_drive_info(
                    drive, temperature=health_info.temperature_celsius
                )

            self._statusbar.showMessage(
                f"NVMe Health loaded — {status.summary}", 5000
            )

        elif data_type == "none":
            self._smart_table.show_message("SMART not supported for this drive")
            self._statusbar.showMessage("SMART not available", 3000)

    def _on_smart_error(self, error_msg: str):
        """Обработчик ошибки чтения SMART."""
        self._smart_table.show_message(f"Error: {error_msg}")
        self._health_indicator.clear()
        self._statusbar.showMessage(f"SMART error: {error_msg}", 5000)
        logger.error(f"SMART read error: {error_msg}")

    def _show_about(self):
        QMessageBox.about(
            self,
            f"About {__app_name__}",
            f"<h3>{__app_name__} v{__version__}</h3>"
            f"<p>Диагностика SSD и HDD дисков</p>"
            f"<p>Windows SMART & NVMe Health Monitor</p>"
            f"<p>Built with Python + PySide6</p>"
        )

    def closeEvent(self, event):
        """Корректное завершение при закрытии окна."""
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(2000)
        event.accept()
