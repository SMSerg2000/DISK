"""Главное окно приложения DISK Diagnostic Tool."""

import logging

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QStatusBar, QMenuBar, QMessageBox, QLabel, QTabWidget,
    QFileDialog,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QKeySequence

from datetime import datetime

from .. import __version__, __app_name__
from ..core.history import save_test
from ..core.models import DriveInfo, DriveType, InterfaceType, HealthLevel, NvmeHealthInfo
from ..data.nvme_fields import NVME_HEALTH_FIELDS
from ..core.drive_enumerator import enumerate_drives
from ..core.smart_ata import read_smart_attributes, read_smart_via_sat, detect_drive_type_from_smart, get_temperature_from_smart
from ..core.smart_nvme import read_nvme_health_auto
from ..core.smart_usb_nvme import read_usb_nvme_smart
from ..core.health_assessor import assess_ata_health, assess_nvme_health
from ..core.winapi import DeviceHandle, DiskAccessError
from .drive_selector import DriveSelector
from ..i18n import tr
from .info_panel import InfoPanel
from .smart_table import SmartTableWidget
from .health_indicator import HealthIndicator
from .benchmark_panel import BenchmarkPanel
from .surface_panel import SurfaceScanPanel

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
            cap = self.drive_info.capacity_bytes
            if self.drive_info.interface_type == InterfaceType.NVME:
                health_info = read_nvme_health_auto(self.drive_info.drive_number)
                status = assess_nvme_health(health_info, cap)
                self.finished.emit(("nvme", health_info, status))
            elif self.drive_info.interface_type == InterfaceType.USB:
                dn = self.drive_info.drive_number
                # 1) USB-SATA: ATA Pass-Through / SCSI SAT
                with DeviceHandle(dn) as h:
                    attrs = read_smart_via_sat(h)
                if attrs:
                    status = assess_ata_health(attrs, cap, self.drive_info.model, self.drive_info.firmware_revision)
                    self.finished.emit(("ata", attrs, status))
                    return
                # 2) USB-NVMe: vendor bridge (JMicron/ASMedia/Realtek)
                logger.info("SAT failed for USB drive, trying USB-NVMe bridges...")
                health_info = read_usb_nvme_smart(dn)
                if health_info:
                    status = assess_nvme_health(health_info, cap)
                    self.finished.emit(("nvme", health_info, status))
                    return
                # 3) Стандартный NVMe IOCTL (может дать WMI fallback)
                try:
                    health_info = read_nvme_health_auto(dn)
                    status = assess_nvme_health(health_info, cap)
                    self.finished.emit(("nvme", health_info, status))
                except Exception:
                    self.finished.emit(("none", None, None))
            elif self.drive_info.smart_supported:
                with DeviceHandle(self.drive_info.drive_number) as h:
                    attrs = read_smart_attributes(h, self.drive_info.drive_number)
                    status = assess_ata_health(attrs, cap, self.drive_info.model, self.drive_info.firmware_revision)
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
        # Для экспорта SMART
        self._smart_data_type: str = "none"   # "ata", "nvme", "none"
        self._smart_ata_attrs: list = []
        self._smart_nvme_health: NvmeHealthInfo | None = None
        self._smart_status: object = None  # HealthStatus

        self._setup_menu()
        self._setup_ui()
        self._setup_statusbar()

        # Загрузка дисков при старте
        self._refresh_drives()

    def _setup_menu(self):
        from ..i18n import save_language
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu(tr("File", "Файл"))
        file_menu.addAction(tr("Refresh", "Обновить"), self._refresh_drives, "F5")
        self._export_action = file_menu.addAction(
            tr("Export SMART...", "Экспорт SMART..."), self._export_smart,
            QKeySequence("Ctrl+S"),
        )
        self._export_action.setEnabled(False)
        file_menu.addAction(
            tr("Export Benchmark...", "Экспорт бенчмарка..."),
            self._export_benchmark, QKeySequence("Ctrl+B"),
        )
        file_menu.addAction(
            tr("Export JSON...", "Экспорт JSON..."),
            self._export_json, QKeySequence("Ctrl+J"),
        )
        file_menu.addSeparator()
        file_menu.addAction(tr("Exit", "Выход"), self.close, "Alt+F4")

        lang_menu = menu_bar.addMenu("🌐 Language")
        lang_menu.addAction("English", lambda: self._switch_language("en"))
        lang_menu.addAction("Русский", lambda: self._switch_language("ru"))

        help_menu = menu_bar.addMenu(tr("Help", "Справка"))
        help_menu.addAction(tr("About", "О программе"), self._show_about)

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

        # Вкладки: SMART + Benchmark
        self._tabs = QTabWidget()

        # SMART tab: таблица + панель описания
        smart_tab = QWidget()
        smart_layout = QVBoxLayout(smart_tab)
        smart_layout.setContentsMargins(0, 0, 0, 0)
        smart_layout.setSpacing(4)

        self._smart_table = SmartTableWidget()
        self._attr_desc = QLabel(tr("Select an attribute to see its description", "Выберите атрибут для просмотра описания"))
        self._attr_desc.setWordWrap(True)
        self._attr_desc.setMinimumHeight(50)
        self._attr_desc.setTextFormat(Qt.TextFormat.RichText)
        self._attr_desc.setStyleSheet(
            "QLabel {"
            "  background-color: #313244;"
            "  color: #cdd6f4;"
            "  padding: 6px 12px;"
            "  border-radius: 6px;"
            "  font-size: 12px;"
            "}"
        )
        self._smart_table.description_changed.connect(self._on_attr_description)

        smart_layout.addWidget(self._smart_table, stretch=1)
        smart_layout.addWidget(self._attr_desc)

        self._benchmark_panel = BenchmarkPanel()
        self._surface_panel = SurfaceScanPanel()
        self._tabs.addTab(smart_tab, "SMART")
        self._tabs.addTab(self._benchmark_panel, tr("Benchmark", "Тесты"))
        self._tabs.addTab(self._surface_panel, tr("Surface Scan", "Поверхность"))
        main_layout.addWidget(self._tabs, stretch=1)

    def _setup_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)

        self._status_admin = QLabel()
        self._status_drives = QLabel()

        self._statusbar.addPermanentWidget(self._status_admin)
        self._statusbar.addPermanentWidget(self._status_drives)

        from ..utils.admin import is_admin
        if is_admin():
            self._status_admin.setText(tr("Admin: Yes", "Админ: Да"))
            self._status_admin.setStyleSheet("color: #a6e3a1;")
        else:
            self._status_admin.setText(tr("Admin: No", "Админ: Нет"))
            self._status_admin.setStyleSheet("color: #f38ba8;")

    def _refresh_drives(self):
        """Перечитать список дисков."""
        self._statusbar.showMessage(tr("Scanning drives...", "Поиск дисков..."), 3000)
        self._info_panel.clear()
        self._health_indicator.clear()
        self._smart_table.show_message(tr("Scanning...", "Поиск..."))
        self._benchmark_panel.clear()
        self._surface_panel.clear()

        try:
            self._drives = enumerate_drives()
            self._drive_selector.set_drives(self._drives)
            self._status_drives.setText(f"{tr("Drives", "Дисков")}: {len(self._drives)}")
            if not self._drives:
                self._smart_table.show_message(tr("No drives found. Run as Administrator.", "Диски не найдены. Запустите от Администратора."))
                self._statusbar.showMessage(tr("No drives found", "Диски не найдены"), 5000)
            else:
                self._statusbar.showMessage(tr("Found", "Найдено") + f" {len(self._drives)} " + tr("drive(s)", "дисков"), 3000)
        except Exception as e:
            logger.exception("Drive enumeration error")
            self._smart_table.show_message(f"{tr("Error", "Ошибка")}: {e}")
            self._statusbar.showMessage(f"{tr("Error", "Ошибка")}: {e}", 5000)

    def _on_drive_selected(self, index: int):
        """Обработчик выбора диска."""
        if index < 0 or index >= len(self._drives):
            return

        drive = self._drives[index]
        self._info_panel.set_drive_info(drive)
        self._health_indicator.clear()
        self._smart_table.show_message(tr("Reading SMART...", "Чтение SMART..."))
        self._benchmark_panel.set_drive(drive.drive_number, drive.capacity_bytes,
                                       drive.interface_type.value, drive.model)
        self._surface_panel.set_drive(drive.drive_number, drive.capacity_bytes,
                                      drive.model)
        self._statusbar.showMessage(f"{tr("Reading SMART", "Чтение SMART")}: {drive.model.strip()}...", 10000)

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
            self._smart_data_type = "ata"
            self._smart_ata_attrs = attrs
            self._smart_status = status
            self._export_action.setEnabled(True)
            drive = self._drive_selector.get_selected_drive()
            self._smart_table.set_ata_attributes(
                attrs, drive.model if drive else "", drive.firmware_revision if drive else "")
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
            self._smart_data_type = "nvme"
            self._smart_nvme_health = health_info
            self._smart_status = status
            self._export_action.setEnabled(True)
            self._smart_table.set_nvme_health(health_info, status)
            self._health_indicator.set_status(status)

            # Обновляем температуру и тип из NVMe
            drive = self._drive_selector.get_selected_drive()
            if drive:
                # USB-NVMe мост: обновляем данные, определённые при перечислении
                if drive.interface_type == InterfaceType.USB:
                    drive.smart_supported = True
                    drive.smart_enabled = True
                    drive.drive_type = DriveType.SSD
                self._info_panel.set_drive_info(
                    drive, temperature=health_info.temperature_celsius
                )

            wmi_note = " (WMI fallback — limited data)" if health_info.wmi_fallback else ""
            self._statusbar.showMessage(
                f"NVMe Health loaded{wmi_note} — {status.summary}", 5000
            )

        # Сохранить в историю
        if self._smart_status and self._smart_status.health_score >= 0:
            drive = self._drive_selector.get_selected_drive()
            if drive:
                s = self._smart_status
                save_test(
                    serial=drive.serial_number or "",
                    model=drive.model.strip(),
                    version=__version__,
                    health_score=s.health_score,
                    tbw_consumed_tb=s.tbw_consumed_tb,
                    power_on_hours=s.power_on_hours,
                    waf=s.waf,
                    penalties=[(r, p) for r, p in s.penalties],
                )

        elif data_type == "none":
            self._smart_data_type = "none"
            self._export_action.setEnabled(False)
            self._smart_table.show_message(tr("SMART not supported for this drive", "SMART не поддерживается для этого диска"))
            self._statusbar.showMessage(tr("SMART not available", "SMART недоступен"), 3000)

    def _on_attr_description(self, html: str):
        """Обновить панель описания атрибута."""
        if html:
            self._attr_desc.setText(html)
        else:
            self._attr_desc.setText(
                '<span style="color: #585b70;">Выберите атрибут для просмотра описания</span>'
            )

    def _on_smart_error(self, error_msg: str):
        """Обработчик ошибки чтения SMART."""
        # Более понятное сообщение для частых ошибок
        if "1117" in error_msg:
            display_msg = (
                "I/O Device Error (1117)\n\n"
                "Drive not responding to SMART commands.\n"
                "Check SATA cable, power connection,\n"
                "or try reconnecting the drive."
            )
        else:
            display_msg = f"Error: {error_msg}"
        self._smart_table.show_message(display_msg)
        self._health_indicator.clear()
        self._statusbar.showMessage(f"SMART error: {error_msg}", 5000)
        logger.error(f"SMART read error: {error_msg}")

    def _export_benchmark(self):
        """Экспорт результатов бенчмарка в текстовый файл."""
        result = self._benchmark_panel._last_result
        if not result:
            QMessageBox.information(self, tr("Export", "Экспорт"),
                tr("No benchmark results.\nRun a test first.",
                   "Нет результатов бенчмарка.\nСначала запустите тест."))
            return

        drive = self._drive_selector.get_selected_drive()
        if not drive:
            return

        safe_model = drive.model.replace(" ", "_").replace("/", "-")
        default_name = f"Benchmark_{safe_model}_{datetime.now():%Y%m%d_%H%M%S}.txt"

        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export Benchmark", "Экспорт бенчмарка"), default_name,
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return

        lines = []
        lines.append(f"{__app_name__} v{__version__} — {tr('Benchmark Report', 'Отчёт бенчмарка')}")
        lines.append(f"{tr('Date', 'Дата')}: {datetime.now():%Y-%m-%d %H:%M:%S}")
        lines.append("=" * 60)
        lines.append(f"{tr('Model', 'Модель')}:      {drive.model}")
        lines.append(f"{tr('Serial', 'Серийный №')}:     {drive.serial_number}")
        lines.append(f"{tr('Capacity', 'Ёмкость')}:   {drive.capacity_bytes / (1024**3):.1f} GB")
        lines.append(f"{tr('Interface', 'Интерфейс')}:  {drive.interface_type.value}")
        lines.append("=" * 60)

        r = result
        lines.append("")
        lines.append(f"{tr('Test', 'Тест'):<25} {tr('Result', 'Результат'):>15} {tr('Details', 'Детали')}")
        lines.append("-" * 60)

        if r.sequential_speed_mbps > 0:
            lines.append(f"{tr('Seq Read', 'Послед. чтение'):<25} {r.sequential_speed_mbps:>12.1f} MB/s")
        if r.seq_write_speed_mbps > 0:
            lines.append(f"{tr('Seq Write', 'Послед. запись'):<25} {r.seq_write_speed_mbps:>12.1f} MB/s")
        if r.random_reads_count > 0:
            lines.append(f"{tr('Random 4K Read', '4K чтение'):<25} {r.random_iops:>12,.0f} IOPS"
                         f"  Avg:{r.random_avg_latency_us:.0f} P95:{r.random_p95_latency_us:.0f} "
                         f"P99:{r.random_p99_latency_us:.0f} P99.9:{r.random_p999_latency_us:.0f} μs")
        if r.random_write_count > 0:
            lines.append(f"{tr('Random 4K Write', '4K запись'):<25} {r.random_write_iops:>12,.0f} IOPS"
                         f"  Avg:{r.random_write_avg_latency_us:.0f} μs")
        if r.mixed_count > 0:
            lines.append(f"{tr('Mixed I/O 70/30', 'Микс 70/30'):<25} {r.mixed_total_iops:>12,.0f} IOPS"
                         f"  R:{r.mixed_read_iops:,.0f} W:{r.mixed_write_iops:,.0f}")
        if r.verify_blocks_tested > 0:
            status = tr("PASS", "ОК") if r.verify_blocks_failed == 0 else f"{tr('FAIL', 'ОШИБКА')} ({r.verify_blocks_failed})"
            lines.append(f"{tr('Write-Read-Verify', 'Проверка записи'):<25} {status:>15}"
                         f"  {r.verify_blocks_tested} {tr('blocks', 'блоков')}, {r.verify_speed_mbps:.1f} MB/s")
        if r.slc_cache_size_gb > 0:
            lines.append(f"{tr('SLC Cache Size', 'Размер SLC кэша'):<25} {r.slc_cache_size_gb:>12.1f} GB"
                         f"  SLC:{r.slc_speed_mbps:.0f} {tr('Post', 'После')}:{r.slc_post_cache_speed_mbps:.0f} MB/s")
        elif r.slc_speed_mbps > 0:
            lines.append(f"{'SLC Cache':<25} {tr('No cliff', 'Без падения'):>15}"
                         f"  {tr('Speed', 'Скорость')}:{r.slc_speed_mbps:.0f} MB/s")

        if r.temp_log:
            lines.append("")
            lines.append(f"{tr('Temperature', 'Температура')}: {r.temp_log[0][1]:.0f}°C ({tr('start', 'начало')}) → "
                         f"{r.temp_log[-1][1]:.0f}°C ({tr('end', 'конец')}), "
                         f"{tr('max', 'макс')} {max(t for _, t in r.temp_log):.0f}°C")

        lines.append("")
        lines.append(f"{tr('Generated by', 'Создано в')} {__app_name__} v{__version__}")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._statusbar.showMessage(f"{tr('Benchmark exported:', 'Бенчмарк экспортирован:')} {path}", 5000)
        except OSError as e:
            QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось записать файл:\n{e}")

    def _export_json(self):
        """Экспорт полной сессии в JSON."""
        import json
        import dataclasses

        drive = self._drive_selector.get_selected_drive()
        if not drive:
            return

        safe_model = drive.model.replace(" ", "_").replace("/", "-")
        default_name = f"DISK_{safe_model}_{datetime.now():%Y%m%d_%H%M%S}.json"

        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export JSON", "Экспорт JSON"), default_name,
            "JSON files (*.json);;All files (*)",
        )
        if not path:
            return

        data = {
            "tool_version": __version__,
            "timestamp": datetime.now().isoformat(),
            "device": {
                "model": drive.model.strip(),
                "serial": drive.serial_number,
                "firmware": drive.firmware_revision,
                "capacity_bytes": drive.capacity_bytes,
                "capacity_gb": round(drive.capacity_bytes / (1024**3), 1),
                "interface": drive.interface_type.value,
                "type": drive.drive_type.value,
            },
        }

        # SMART
        if self._smart_status:
            s = self._smart_status
            data["health"] = {
                "score": s.health_score,
                "level": s.level.value,
                "summary": s.summary,
                "penalties": [{"reason": r, "points": p} for r, p in s.penalties],
                "warnings": s.warnings,
                "critical_issues": s.critical_issues,
                "tbw_consumed_tb": s.tbw_consumed_tb,
                "tbw_rated_tb": s.tbw_rated_tb,
                "tbw_remaining_days": s.tbw_remaining_days,
                "daily_write_tb": s.daily_write_tb,
                "power_on_hours": s.power_on_hours,
                "waf": s.waf,
            }

        if self._smart_data_type == "ata" and self._smart_ata_attrs:
            data["smart_ata"] = [
                {
                    "id": a.id, "name": a.name,
                    "current": a.current, "worst": a.worst,
                    "threshold": a.threshold, "raw_value": a.raw_value,
                    "health": a.health_level.value,
                }
                for a in self._smart_ata_attrs
            ]
        elif self._smart_data_type == "nvme" and self._smart_nvme_health:
            h = self._smart_nvme_health
            data["smart_nvme"] = {
                "critical_warning": h.critical_warning,
                "temperature_celsius": h.temperature_celsius,
                "available_spare": h.available_spare,
                "available_spare_threshold": h.available_spare_threshold,
                "percentage_used": h.percentage_used,
                "data_units_read": h.data_units_read,
                "data_units_written": h.data_units_written,
                "host_read_commands": h.host_read_commands,
                "host_write_commands": h.host_write_commands,
                "controller_busy_time": h.controller_busy_time,
                "power_cycles": h.power_cycles,
                "power_on_hours": h.power_on_hours,
                "unsafe_shutdowns": h.unsafe_shutdowns,
                "media_errors": h.media_errors,
                "error_log_entries": h.error_log_entries,
                "warning_temp_time": h.warning_temp_time,
                "critical_temp_time": h.critical_temp_time,
                "wmi_fallback": h.wmi_fallback,
            }

        # Benchmark
        result = self._benchmark_panel._last_result
        if result:
            data["benchmark"] = {
                "sequential_read_mbps": result.sequential_speed_mbps,
                "sequential_write_mbps": result.seq_write_speed_mbps,
                "random_4k_read_iops": result.random_iops,
                "random_4k_read_avg_us": result.random_avg_latency_us,
                "random_4k_read_p95_us": result.random_p95_latency_us,
                "random_4k_read_p99_us": result.random_p99_latency_us,
                "random_4k_read_p999_us": result.random_p999_latency_us,
                "random_4k_read_p9999_us": result.random_p9999_latency_us,
                "random_4k_write_iops": result.random_write_iops,
                "mixed_io_total_iops": result.mixed_total_iops,
                "mixed_io_read_iops": result.mixed_read_iops,
                "mixed_io_write_iops": result.mixed_write_iops,
                "verify_blocks_tested": result.verify_blocks_tested,
                "verify_blocks_failed": result.verify_blocks_failed,
                "slc_cache_size_gb": result.slc_cache_size_gb,
                "slc_speed_mbps": result.slc_speed_mbps,
                "slc_post_cache_speed_mbps": result.slc_post_cache_speed_mbps,
                "sweep_points": result.sweep_points,
                "slc_points": result.slc_points,
                "temp_log": result.temp_log,
                "io_errors": result.io_errors,
            }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._statusbar.showMessage(
                f"{tr('JSON exported:', 'JSON экспортирован:')} {path}", 5000)
        except OSError as e:
            QMessageBox.critical(self, tr("Export Error", "Ошибка экспорта"),
                                 f"{tr('Cannot write file:', 'Не удалось записать файл:')}\n{e}")

    def _export_smart(self):
        """Экспорт SMART данных в текстовый файл."""
        drive = self._drive_selector.get_selected_drive()
        if not drive:
            return

        # Имя файла по умолчанию
        safe_model = drive.model.replace(" ", "_").replace("/", "-")
        default_name = f"SMART_{safe_model}_{datetime.now():%Y%m%d_%H%M%S}.txt"

        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export SMART", "Экспорт SMART"), default_name,
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return

        lines = []
        lines.append(f"{__app_name__} v{__version__} — {tr('SMART Report', 'Отчёт SMART')}")
        lines.append(f"{tr('Date', 'Дата')}: {datetime.now():%Y-%m-%d %H:%M:%S}")
        lines.append("=" * 70)
        lines.append(f"{tr('Model', 'Модель')}:      {drive.model}")
        lines.append(f"{tr('Serial', 'Серийный №')}:     {drive.serial_number}")
        lines.append(f"{tr('Firmware', 'Прошивка')}:   {drive.firmware_revision}")
        lines.append(f"{tr('Capacity', 'Ёмкость')}:   {drive.capacity_bytes / (1024**3):.1f} GB")
        lines.append(f"{tr('Interface', 'Интерфейс')}:  {drive.interface_type.value}")
        lines.append(f"{tr('Type', 'Тип')}:       {drive.drive_type.value}")
        lines.append("=" * 70)

        # Оценка здоровья
        if self._smart_status:
            s = self._smart_status
            level = s.level.value.upper()
            lines.append(f"\n{tr('Health', 'Здоровье')}:     {level} — {s.summary}")
            if s.health_score >= 0:
                lines.append(f"Score:      {s.health_score}/100")
            if s.penalties:
                for reason, pts in s.penalties:
                    pts_lbl = tr("pt", "балл") if pts == 1 else tr("pts", "балла" if pts < 5 else "баллов")
                    lines.append(f"  −{pts} {pts_lbl}: {reason}")
            if s.tbw_consumed_tb > 0:
                lines.append(f"TBW {tr('used', 'использовано')}:   {s.tbw_consumed_tb:.1f} TB")
            if s.tbw_rated_tb > 0:
                lines.append(f"TBW {tr('rated', 'номинал')}:  ~{s.tbw_rated_tb:.0f} TB ({tr('estimate', 'оценка')})")
            if s.tbw_remaining_days > 0:
                years = s.tbw_remaining_days / 365
                lbl = tr("Forecast", "Прогноз")
                if years > 100:
                    lines.append(f"{lbl}:    > 100 {tr('years', 'лет')}")
                else:
                    lines.append(f"{lbl}:    ~{years:.1f} {tr('years', 'лет')} ({s.tbw_remaining_days} {tr('days', 'дней')})")
            if s.daily_write_tb > 0:
                lines.append(f"{tr('Write/day', 'Запись/день')}: {s.daily_write_tb * 1024:.1f} GB/{tr('day', 'день')}")
            if s.waf > 0:
                lines.append(f"WAF:        {s.waf:.2f}")
            for w in s.warnings:
                lines.append(f"  [!] {w}")
            for c in s.critical_issues:
                lines.append(f"  [!!!] {c}")

        if self._smart_data_type == "ata":
            lines.append("")
            lines.append(f"{'ID':<5} {tr('Attribute', 'Атрибут'):<30} {tr('Cur', 'Текущ'):>5} {tr('Wst', 'Худш'):>5} "
                         f"{tr('Thr', 'Порог'):>5} {tr('Raw Value', 'Raw значение'):>16}  {tr('Status', 'Статус')}")
            lines.append("-" * 85)
            for a in self._smart_ata_attrs:
                status = "OK" if a.health_level == HealthLevel.GOOD else (
                    "WARN" if a.health_level == HealthLevel.WARNING else
                    "CRIT" if a.health_level == HealthLevel.CRITICAL else "—"
                )
                lines.append(
                    f"{a.id:<5} {a.name:<30} {a.current:>5} {a.worst:>5} "
                    f"{a.threshold:>5} {a.raw_value:>16,}  {status}"
                )

        elif self._smart_data_type == "nvme":
            h = self._smart_nvme_health
            lines.append("")
            lines.append(f"{tr('Parameter', 'Параметр'):<40} {tr('Value', 'Значение'):>20}")
            lines.append("-" * 62)

            nvme_rows = [
                ("critical_warning", h.critical_warning),
                ("temperature_celsius", h.temperature_celsius),
                ("available_spare", h.available_spare),
                ("available_spare_threshold", h.available_spare_threshold),
                ("percentage_used", h.percentage_used),
                ("data_units_read", h.data_units_read),
                ("data_units_written", h.data_units_written),
                ("host_read_commands", h.host_read_commands),
                ("host_write_commands", h.host_write_commands),
                ("controller_busy_time", h.controller_busy_time),
                ("power_cycles", h.power_cycles),
                ("power_on_hours", h.power_on_hours),
                ("unsafe_shutdowns", h.unsafe_shutdowns),
                ("media_errors", h.media_errors),
                ("error_log_entries", h.error_log_entries),
                ("warning_temp_time", h.warning_temp_time),
                ("critical_temp_time", h.critical_temp_time),
            ]
            for key, val in nvme_rows:
                fi = NVME_HEALTH_FIELDS.get(key)
                name = fi.name if fi else key
                unit = f" {fi.unit}" if fi and fi.unit else ""
                lines.append(f"{name:<40} {val:>18,}{unit}")

            if h.wmi_fallback:
                lines.append("")
                lines.append(tr("(!) Data source: WMI fallback — limited data",
                                "(!) Источник: WMI fallback — ограниченные данные"))

        lines.append("")
        lines.append(f"{tr('Generated by', 'Создано в')} {__app_name__} v{__version__}")

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            self._statusbar.showMessage(f"{tr('SMART exported:', 'SMART экспортирован:')} {path}", 5000)
        except OSError as e:
            QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось записать файл:\n{e}")

    def _switch_language(self, lang: str):
        from ..i18n import save_language, get_language
        if lang == get_language():
            return
        save_language(lang)
        QMessageBox.information(
            self, "Language / Язык",
            "Language changed. Please restart the application.\n"
            "Язык изменён. Перезапустите программу.",
        )

    def _show_about(self):
        QMessageBox.about(
            self,
            f"{tr("About", "О программе")} {__app_name__}",
            f"<h3>{__app_name__} v{__version__}</h3>"
            f"<p>Диагностика SSD и HDD дисков</p>"
            f"<p>Windows SMART & NVMe Health Monitor</p>"
            f"<p>Built with Python + PySide6</p>"
        )

    def closeEvent(self, event):
        """Корректное завершение при закрытии окна."""
        self._benchmark_panel.stop()
        self._surface_panel.stop()
        if self._worker_thread is not None and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(2000)
        event.accept()
