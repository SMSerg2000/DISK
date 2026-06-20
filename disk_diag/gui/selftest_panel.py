"""Панель Self-test: запуск SMART/NVMe самопроверки + журнал результатов.

Первая «активная» вкладка: командует диску провести внутреннюю самопроверку
(Short/Extended), опрашивает прогресс раз в несколько секунд и показывает журнал
прошлых тестов, прочитанный из самого диска. Non-destructive.

Особенность жизненного цикла: self-test исполняется в firmware диска. Закрытие
окна лишь прекращает опрос — тест продолжается. Кнопка Abort, наоборот, шлёт
диску команду прерывания.
"""

import logging
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QProgressBar, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QMessageBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QColor, QBrush

from ..core.self_test import SelfTestEngine
from ..core.models import SelfTestType, SelfTestLog, InterfaceType
from ..i18n import tr

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SEC = 5.0       # период опроса прогресса
_OK_BG = QColor(64, 90, 70)    # тёмно-зелёный фон для пройденных
_FAIL_BG = QColor(95, 60, 70)  # тёмно-красный фон для проваленных


# ---------------------------------------------------------------------------
#  Worker
# ---------------------------------------------------------------------------

class _SelfTestWorker(QObject):
    """Запускает self-test и опрашивает прогресс в фоновом потоке."""

    started_ok = Signal()             # тест успешно стартовал
    progress = Signal(int, str)       # percent (-1 = неизвестно), message
    finished = Signal(object)         # SelfTestLog (итоговый журнал)
    error = Signal(str)

    def __init__(self, engine: SelfTestEngine, test_type: SelfTestType):
        super().__init__()
        self._engine = engine
        self._test_type = test_type
        self._cancelled = False       # прекратить опрос (закрытие окна)
        self._abort_test = False      # прервать сам тест на диске (кнопка Abort)

    def run(self):
        try:
            self._engine.start(self._test_type)
            self.started_ok.emit()
            self.progress.emit(0, tr("Self-test started…", "Самотест запущен…"))

            seen_running = False
            attempts = 0
            poll_errors = 0
            while not self._cancelled:
                self._interruptible_sleep(_POLL_INTERVAL_SEC)
                if self._cancelled:
                    break
                try:
                    state = self._engine.poll()
                    poll_errors = 0
                except Exception as e:
                    # Разовый сбой опроса (USB-реконнект и т.п.) — не валим сразу
                    poll_errors += 1
                    logger.debug(f"Self-test poll failed ({poll_errors}): {e}")
                    if poll_errors >= 3:
                        raise
                    continue

                attempts += 1
                if state.running:
                    seen_running = True
                    msg = (tr("Running…", "Выполняется…") if state.percent < 0
                           else f"{state.percent}%")
                    self.progress.emit(state.percent, msg)
                elif seen_running:
                    break  # тест завершился
                elif attempts >= 3:
                    # Диск так и не показал "в процессе" — вероятно, тест уже
                    # прошёл (быстрый short на SSD) или не стартовал
                    break

            if self._abort_test:
                try:
                    self._engine.abort()
                except Exception as e:
                    logger.debug(f"Self-test abort failed: {e}")

            log = self._engine.read_log()
            self.finished.emit(log)
        except Exception as e:
            logger.exception("Self-test worker error")
            self.error.emit(str(e))

    def _interruptible_sleep(self, seconds: float):
        """Спим короткими квантами, чтобы быстро реагировать на cancel."""
        elapsed = 0.0
        while elapsed < seconds and not self._cancelled:
            time.sleep(0.2)
            elapsed += 0.2

    def cancel(self):
        """Прекратить опрос (тест продолжается в firmware) — для закрытия окна."""
        self._cancelled = True

    def request_abort(self):
        """Прервать self-test на диске (кнопка Abort)."""
        self._abort_test = True
        self._cancelled = True


# ---------------------------------------------------------------------------
#  Panel
# ---------------------------------------------------------------------------

class SelfTestPanel(QWidget):
    """Панель запуска самопроверки диска и просмотра журнала."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drive_number: int | None = None
        self._capacity_bytes: int = 0
        self._interface: str = ""
        self._model: str = ""
        self._serial: str = ""
        self._worker: _SelfTestWorker | None = None
        self._thread: QThread | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- Controls ---
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._btn_start = QPushButton(tr("▶  Start Self-test", "▶  Запустить самотест"))
        self._btn_start.setFixedHeight(36)
        self._btn_start.setEnabled(False)
        self._btn_start.clicked.connect(self._start_self_test)

        self._btn_abort = QPushButton(tr("■  Abort", "■  Прервать"))
        self._btn_abort.setFixedHeight(36)
        self._btn_abort.setEnabled(False)
        self._btn_abort.clicked.connect(self._abort_self_test)

        self._type_combo = QComboBox()
        self._type_combo.setFixedHeight(36)
        self._type_combo.setMinimumWidth(150)
        self._type_combo.addItem(tr("Short (~1-2 min)", "Короткий (~1-2 мин)"), SelfTestType.SHORT)
        self._type_combo.addItem(tr("Extended (minutes-hours)", "Расширенный (минуты-часы)"), SelfTestType.EXTENDED)

        self._btn_reload = QPushButton(tr("🔄  Reload log", "🔄  Обновить журнал"))
        self._btn_reload.setFixedHeight(36)
        self._btn_reload.setEnabled(False)
        self._btn_reload.clicked.connect(self._load_log)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(24)

        self._status = QLabel(tr("Select a drive", "Выберите диск"))
        self._status.setStyleSheet("color: #a6adc8;")

        controls.addWidget(self._btn_start)
        controls.addWidget(self._btn_abort)
        controls.addWidget(self._type_combo)
        controls.addWidget(self._btn_reload)
        controls.addWidget(self._progress, stretch=1)
        controls.addWidget(self._status)
        layout.addLayout(controls)

        # --- Info / caveat ---
        self._info = QLabel(tr(
            "Self-test is non-destructive — the drive checks itself, your data is untouched. "
            "Extended can take hours; you can keep using the PC (slower) and Abort anytime. "
            "Closing the app does not stop a running test (the drive keeps going).",
            "Самотест non-destructive — диск проверяет сам себя, данные не трогаются. "
            "Расширенный может идти часами; ПК можно использовать (медленнее) и прервать в любой момент. "
            "Закрытие программы не останавливает тест (диск продолжает сам)."))
        self._info.setWordWrap(True)
        self._info.setStyleSheet("color: #6c7086; font-size: 11px;")
        layout.addWidget(self._info)

        # --- History table ---
        hist_label = QLabel(tr("Self-test history (read from the drive):",
                               "Журнал самопроверок (из диска):"))
        hist_label.setStyleSheet("color: #a6adc8; font-weight: bold;")
        layout.addWidget(hist_label)

        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            "#",
            tr("Test", "Тест"),
            tr("Status", "Статус"),
            tr("Power-on hours", "Наработка, ч"),
            tr("First error LBA", "LBA первой ошибки"),
        ])
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, stretch=1)

    # --- Public API (контракт панели) ---

    def set_drive(self, drive_number: int, capacity_bytes: int,
                  interface_type: str = "", model: str = "", serial: str = ""):
        """Установить диск для самопроверки."""
        self.stop()
        self._drive_number = drive_number
        self._capacity_bytes = capacity_bytes
        self._interface = interface_type
        self._model = model
        self._serial = serial

        is_virtual = interface_type == InterfaceType.VIRTUAL.value
        self._btn_start.setEnabled(not is_virtual)
        self._btn_reload.setEnabled(not is_virtual)
        self._btn_abort.setEnabled(False)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._table.setRowCount(0)

        if is_virtual:
            self._set_status(tr("Not available for virtual disks",
                                "Недоступно для виртуальных дисков"), "#6c7086")
            return

        self._set_status(tr("Ready", "Готов"), "#a6adc8")
        self._load_log()  # показать прошлые тесты сразу (один короткий IOCTL)

    def clear(self):
        """Полный сброс панели."""
        self.stop()
        self._drive_number = None
        self._capacity_bytes = 0
        self._btn_start.setEnabled(False)
        self._btn_reload.setEnabled(False)
        self._btn_abort.setEnabled(False)
        self._table.setRowCount(0)
        self._progress.setValue(0)
        self._set_status(tr("Select a drive", "Выберите диск"), "#a6adc8")

    def is_running(self) -> bool:
        """Идёт ли опрос self-test (для подтверждения при закрытии окна)."""
        return bool(self._thread and self._thread.isRunning())

    def stop(self):
        """Прекратить ОПРОС (сам тест в firmware продолжается). Для закрытия окна."""
        if self._worker:
            self._worker.cancel()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            if not self._thread.wait(3000):
                logger.warning("Self-test poll thread still running, waiting up to 7s...")
                self._thread.wait(7000)
        self._worker = None
        self._thread = None
        self._btn_abort.setEnabled(False)
        self._btn_start.setEnabled(self._drive_number is not None
                                   and self._interface != InterfaceType.VIRTUAL.value)

    # --- Private ---

    def _engine(self) -> SelfTestEngine:
        return SelfTestEngine(self._drive_number, self._interface,
                              self._model, self._serial)

    def _set_status(self, text: str, color: str):
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _load_log(self):
        """Прочитать журнал самопроверок из диска (read-only, безопасно)."""
        if self._drive_number is None:
            return
        try:
            log = self._engine().read_log()
        except Exception as e:
            logger.debug(f"Reload self-test log failed: {e}")
            self._set_status(tr("Log unavailable", "Журнал недоступен"), "#f9e2af")
            return
        self._fill_table(log)

    def _start_self_test(self):
        if self._drive_number is None:
            return
        test_type = self._type_combo.currentData()

        if test_type == SelfTestType.EXTENDED:
            reply = QMessageBox.information(
                self, tr("Extended Self-test", "Расширенный самотест"),
                tr("The extended self-test can take from several minutes to many hours "
                   "depending on disk size.\n\nIt is non-destructive and runs in the "
                   "drive firmware — you can keep using the PC and Abort anytime.\n\nStart now?",
                   "Расширенный самотест может занять от нескольких минут до многих часов "
                   "в зависимости от размера диска.\n\nОн non-destructive и выполняется в "
                   "firmware диска — можно продолжать работать и прервать в любой момент.\n\nЗапустить?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Утилизируем предыдущие worker/thread (соединения не должны жить дальше)
        if self._worker is not None:
            try:
                self._worker.disconnect()
            except RuntimeError:
                pass
            self._worker.deleteLater()
            self._worker = None
        if self._thread is not None:
            self._thread.deleteLater()
            self._thread = None

        self._worker = _SelfTestWorker(self._engine(), test_type)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.started_ok.connect(self._on_started)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._btn_start.setEnabled(False)
        self._btn_reload.setEnabled(False)
        self._type_combo.setEnabled(False)
        self._progress.setRange(0, 0)  # indeterminate, пока не пришёл первый %
        self._set_status(tr("Starting…", "Запуск…"), "#cdd6f4")
        self._thread.start()

    def _abort_self_test(self):
        if self._worker:
            self._worker.request_abort()
        self._set_status(tr("Aborting…", "Прерывание…"), "#f9e2af")
        self._btn_abort.setEnabled(False)

    def _on_started(self):
        self._btn_abort.setEnabled(True)
        self._set_status(tr("Running…", "Выполняется…"), "#cdd6f4")

    def _on_progress(self, percent: int, message: str):
        if percent < 0:
            self._progress.setRange(0, 0)  # indeterminate
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(percent)
        self._set_status(message, "#cdd6f4")

    def _on_finished(self, log: SelfTestLog):
        # Прогон опроса завершён
        self._worker = None
        self._progress.setRange(0, 100)
        self._progress.setValue(100)
        self._type_combo.setEnabled(True)
        self._btn_abort.setEnabled(False)
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_reload.setEnabled(self._drive_number is not None)

        self._fill_table(log)

        # Итоговый статус по самой свежей записи
        if not log.supported:
            self._set_status(tr("Not supported", "Не поддерживается"), "#f9e2af")
        elif log.entries:
            latest = log.entries[0]
            if latest.passed:
                self._set_status(tr("Done — passed ✓", "Готово — пройден ✓"), "#a6e3a1")
            else:
                self._set_status(tr("Done — FAILED ✗", "Готово — ОШИБКА ✗"), "#f38ba8")
        else:
            self._set_status(tr("Done", "Готово"), "#a6e3a1")

    def _on_error(self, error_msg: str):
        self._worker = None
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._type_combo.setEnabled(True)
        self._btn_abort.setEnabled(False)
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_reload.setEnabled(self._drive_number is not None)
        self._set_status(tr("Error", "Ошибка"), "#f38ba8")
        QMessageBox.warning(
            self, tr("Self-test Error", "Ошибка самотеста"),
            tr(f"Could not run self-test on this drive:\n\n{error_msg}\n\n"
               f"Some USB bridges and OEM drivers do not expose self-test.",
               f"Не удалось запустить самотест на этом диске:\n\n{error_msg}\n\n"
               f"Некоторые USB-мосты и OEM-драйверы не поддерживают самотест."),
        )

    def _fill_table(self, log: SelfTestLog):
        self._table.setRowCount(0)
        if not log.supported:
            self._set_status(log.note or tr("Not supported", "Не поддерживается"), "#f9e2af")
            return
        entries = log.entries
        self._table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            bg = _OK_BG if e.passed else _FAIL_BG
            poh = str(e.lifetime_hours) if e.lifetime_hours >= 0 else "—"
            lba = str(e.failing_lba) if e.failing_lba >= 0 else "—"
            cells = [str(row + 1), e.test_description, e.status_text, poh, lba]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (0, 3, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setBackground(QBrush(bg))
                self._table.setItem(row, col, item)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
