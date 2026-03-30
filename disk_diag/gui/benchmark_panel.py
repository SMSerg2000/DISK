"""Панель бенчмарка: чтение, запись, SLC-кэш тест, scatter plot латентности."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QProgressBar, QLabel, QCheckBox, QMessageBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QRectF, QPointF
from PySide6.QtGui import QFont, QPainter, QColor, QBrush, QPen

from ..core.benchmark import BenchmarkEngine
from ..core.models import BenchmarkResult
from ..i18n import tr

# noinspection PyUnresolvedReferences — used in _on_finished for SLC_MAX_GB

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Worker (QThread)
# ---------------------------------------------------------------------------

class _BenchmarkWorker(QObject):
    """Фоновый воркер: запускает BenchmarkEngine и шлёт сигналы в GUI."""

    progress = Signal(str, float, str)   # phase, 0..1, message
    finished = Signal(object)            # BenchmarkResult
    error = Signal(str)

    def __init__(self, drive_number: int, capacity_bytes: int,
                 include_write: bool = False, interface_type: str = ""):
        super().__init__()
        self._engine = BenchmarkEngine(drive_number, capacity_bytes,
                                       include_write, interface_type)

    def run(self):
        try:
            result = self._engine.run(
                progress=lambda phase, pct, msg: self.progress.emit(phase, pct, msg)
            )
            self.finished.emit(result)
        except Exception as e:
            logger.exception("Benchmark error")
            self.error.emit(str(e))

    def cancel(self):
        self._engine.cancel()


# ---------------------------------------------------------------------------
#  Line Chart Widget (SLC Cache / Drive Sweep)
# ---------------------------------------------------------------------------

class LineChartWidget(QWidget):
    """Универсальный line chart: speed (MB/s) vs position/volume (GB)."""

    def __init__(self, title: str = "", x_label: str = "GB",
                 y_label: str = "MB/s", parent=None):
        super().__init__(parent)
        self._points: list[tuple[float, float]] = []
        self._title = title
        self._x_label = x_label
        self._y_label = y_label
        self.setMinimumHeight(150)

    def set_points(self, points: list[tuple[float, float]]):
        self._points = points
        self.update()

    def clear(self):
        self._points.clear()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(30, 30, 46))

        font = QFont("Segoe UI", 9)
        painter.setFont(font)

        margin_l, margin_r, margin_t, margin_b = 55, 15, 25, 30
        plot = QRectF(margin_l, margin_t,
                      rect.width() - margin_l - margin_r,
                      rect.height() - margin_t - margin_b)
        pw, ph = plot.width(), plot.height()

        # Title
        if self._title:
            painter.setPen(QColor(205, 214, 244))
            painter.drawText(QRectF(0, 2, rect.width(), 20),
                             Qt.AlignmentFlag.AlignCenter, self._title)

        # Axes
        painter.setPen(QPen(QColor(88, 91, 112), 1))
        painter.drawLine(int(plot.left()), int(plot.bottom()),
                         int(plot.right()), int(plot.bottom()))
        painter.drawLine(int(plot.left()), int(plot.top()),
                         int(plot.left()), int(plot.bottom()))

        if not self._points or len(self._points) < 2:
            painter.setPen(QColor(88, 91, 112))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, tr("No data", "Нет данных"))
            painter.end()
            return

        max_x = max(p[0] for p in self._points)
        max_y = max(p[1] for p in self._points) * 1.1
        if max_x <= 0 or max_y <= 0:
            painter.end()
            return

        # Y-axis labels
        painter.setPen(QColor(166, 173, 200))
        for i in range(5):
            y_val = max_y * i / 4
            y = plot.bottom() - (i / 4) * ph
            painter.drawText(QRectF(0, y - 8, margin_l - 5, 16),
                             Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             f"{y_val:.0f}")

        # X-axis labels
        for i in range(5):
            x_val = max_x * i / 4
            x = plot.left() + (i / 4) * pw
            painter.drawText(QRectF(x - 20, plot.bottom() + 2, 40, 16),
                             Qt.AlignmentFlag.AlignCenter, f"{x_val:.0f}")

        # Line
        painter.setPen(QPen(QColor(137, 180, 250), 2))  # blue
        prev = None
        for x_val, y_val in self._points:
            x = plot.left() + (x_val / max_x) * pw
            y = plot.bottom() - (y_val / max_y) * ph
            y = max(plot.top(), min(plot.bottom(), y))
            if prev:
                painter.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
            prev = (x, y)

        painter.end()


# ---------------------------------------------------------------------------
#  Latency Histogram Widget
# ---------------------------------------------------------------------------

class LatencyHistogramWidget(QWidget):
    """Гистограмма распределения латентности (Random 4K Read)."""

    BINS = [
        (0, 50, "0-50"),
        (50, 100, "50-100"),
        (100, 200, "100-200"),
        (200, 500, "200-500"),
        (500, 1000, "500-1K"),
        (1000, float('inf'), ">1K"),
    ]
    BIN_COLORS = [
        QColor(166, 227, 161),   # green
        QColor(148, 226, 213),   # teal
        QColor(249, 226, 175),   # yellow
        QColor(250, 179, 135),   # peach
        QColor(243, 139, 168),   # red
        QColor(235, 160, 172),   # maroon
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._counts: list[int] = []
        self._total = 0
        self.setMinimumHeight(150)

    def set_latencies(self, latencies_us: list[float]):
        self._counts = [0] * len(self.BINS)
        for lat in latencies_us:
            for i, (lo, hi, _) in enumerate(self.BINS):
                if lo <= lat < hi:
                    self._counts[i] += 1
                    break
        self._total = len(latencies_us)
        self.update()

    def clear(self):
        self._counts.clear()
        self._total = 0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(30, 30, 46))

        font = QFont("Segoe UI", 9)
        painter.setFont(font)

        margin_l, margin_r, margin_t, margin_b = 45, 10, 20, 30
        plot = QRectF(margin_l, margin_t,
                      rect.width() - margin_l - margin_r,
                      rect.height() - margin_t - margin_b)

        # Title
        painter.setPen(QColor(205, 214, 244))
        painter.drawText(QRectF(0, 2, rect.width(), 16),
                         Qt.AlignmentFlag.AlignCenter, tr("Latency Distribution (μs)", "Распределение задержек (мкс)"))

        if not self._counts or self._total == 0:
            painter.setPen(QColor(88, 91, 112))
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, tr("No data", "Нет данных"))
            painter.end()
            return

        max_count = max(self._counts) if self._counts else 1
        n_bins = len(self.BINS)
        bar_w = plot.width() / n_bins * 0.8
        gap = plot.width() / n_bins * 0.2

        for i, (count, (lo, hi, label)) in enumerate(zip(self._counts, self.BINS)):
            x = plot.left() + i * (bar_w + gap)
            if max_count > 0:
                h = (count / max_count) * plot.height()
            else:
                h = 0
            y = plot.bottom() - h

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.BIN_COLORS[i])
            painter.drawRect(QRectF(x, y, bar_w, h))

            # Label
            painter.setPen(QColor(166, 173, 200))
            painter.drawText(QRectF(x, plot.bottom() + 2, bar_w, 16),
                             Qt.AlignmentFlag.AlignCenter, label)

            # Count
            if count > 0:
                pct = count / self._total * 100
                painter.setPen(QColor(205, 214, 244))
                painter.drawText(QRectF(x, y - 14, bar_w, 14),
                                 Qt.AlignmentFlag.AlignCenter,
                                 f"{pct:.0f}%")

        painter.end()


# ---------------------------------------------------------------------------
#  Latency Scatter Plot
# ---------------------------------------------------------------------------

class LatencyScatterWidget(QWidget):
    """Scatter plot: латентность (μs) vs позиция на диске (GB)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[tuple[float, float]] = []
        self._max_latency = 100.0
        self._max_offset = 1.0
        self.setMinimumHeight(200)

    def clear(self):
        self._points.clear()
        self._max_latency = 100.0
        self._max_offset = 1.0
        self.update()

    def set_points(self, points: list[tuple[float, float]]):
        self._points = list(points)
        if points:
            self._max_latency = max(p[1] for p in points) * 1.2
            self._max_offset = max(max(p[0] for p in points) * 1.1, 0.1)
            if self._max_latency <= 0:
                self._max_latency = 100.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        painter.fillRect(rect, QColor(30, 30, 46))  # Catppuccin base

        ml, mr, mt, mb = 65, 20, 15, 45
        pw = rect.width() - ml - mr
        ph = rect.height() - mt - mb

        if pw <= 0 or ph <= 0:
            painter.end()
            return

        plot = QRectF(ml, mt, pw, ph)

        # --- Grid ---
        pen = QPen(QColor(69, 71, 90))  # surface1
        pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        for i in range(1, 4):
            y = plot.top() + ph * i / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            x = plot.left() + pw * i / 4
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        # --- Axes ---
        pen = QPen(QColor(88, 91, 112))  # overlay0
        pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))
        painter.drawLine(QPointF(plot.left(), plot.top()), QPointF(plot.left(), plot.bottom()))

        # --- Labels ---
        painter.setPen(QColor(166, 172, 205))  # subtext0
        font = QFont("Segoe UI", 8)
        painter.setFont(font)

        # Y axis (latency)
        for i in range(5):
            y = plot.top() + ph * (4 - i) / 4
            val = self._max_latency * i / 4
            if val >= 1000:
                label = f"{val / 1000:.1f}ms"
            else:
                label = f"{val:.0f}μs"
            painter.drawText(
                QRectF(0, y - 8, ml - 5, 16),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

        # X axis (offset)
        for i in range(5):
            x = plot.left() + pw * i / 4
            val = self._max_offset * i / 4
            painter.drawText(
                QRectF(x - 25, plot.bottom() + 3, 50, 16),
                Qt.AlignmentFlag.AlignCenter,
                f"{val:.0f} GB",
            )

        # Axis title
        painter.setPen(QColor(205, 214, 244))  # text
        painter.drawText(
            QRectF(plot.left(), plot.bottom() + 22, pw, 16),
            Qt.AlignmentFlag.AlignCenter,
            tr("Disk Position (GB)", "Позиция на диске (ГБ)"),
        )

        # --- No data ---
        if not self._points:
            painter.setPen(QColor(88, 91, 112))
            font.setPointSize(11)
            painter.setFont(font)
            painter.drawText(
                plot,
                Qt.AlignmentFlag.AlignCenter,
                tr("Run benchmark to see chart", "Запустите тест для отображения графика"),
            )
            painter.end()
            return

        # --- Points ---
        painter.setPen(Qt.PenStyle.NoPen)
        for offset_gb, latency_us in self._points:
            x = plot.left() + (offset_gb / self._max_offset) * pw
            y = plot.bottom() - (latency_us / self._max_latency) * ph

            # Clamp
            y = max(plot.top(), min(plot.bottom(), y))
            x = max(plot.left(), min(plot.right(), x))

            ratio = latency_us / self._max_latency
            if ratio > 0.6:
                color = QColor(243, 139, 168, 200)   # red
            elif ratio > 0.25:
                color = QColor(249, 226, 175, 200)   # yellow
            else:
                color = QColor(166, 227, 161, 200)    # green

            painter.setBrush(QBrush(color))
            painter.drawEllipse(QPointF(x, y), 3, 3)

        painter.end()


# ---------------------------------------------------------------------------
#  Result card
# ---------------------------------------------------------------------------

class _ResultCard(QGroupBox):
    """Карточка с результатом теста (большая цифра + детали)."""

    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._value = QLabel("—")
        self._value.setFont(QFont("Segoe UI", 26, QFont.Weight.Bold))
        self._value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._value.setStyleSheet("color: #cdd6f4;")

        self._detail = QLabel("")
        self._detail.setFont(QFont("Segoe UI", 9))
        self._detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail.setStyleSheet("color: #a6adc8;")
        self._detail.setWordWrap(True)

        layout.addWidget(self._value)
        layout.addWidget(self._detail)

    def set_result(self, value: str, detail: str = ""):
        self._value.setText(value)
        self._detail.setText(detail)

    def clear(self):
        self._value.setText("—")
        self._detail.setText("")


# ---------------------------------------------------------------------------
#  Benchmark Panel
# ---------------------------------------------------------------------------

class BenchmarkPanel(QWidget):
    """Панель бенчмарка: кнопки, прогресс, результаты, scatter plot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drive_number: int | None = None
        self._capacity_bytes: int = 0
        self._interface_type: str = ""
        self._model: str = ""
        self._last_result: BenchmarkResult | None = None
        self._worker: _BenchmarkWorker | None = None
        self._thread: QThread | None = None

        self._setup_ui()

    # --- UI setup ---

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # --- Controls row ---
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._btn_start = QPushButton(tr("▶  Start Benchmark", "▶  Запустить тест"))
        self._btn_start.setFixedHeight(36)
        self._btn_start.setEnabled(False)
        self._btn_start.clicked.connect(self._start_benchmark)

        self._btn_stop = QPushButton(tr("■  Stop", "■  Стоп"))
        self._btn_stop.setFixedHeight(36)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_benchmark)

        self._write_check = QCheckBox(tr("+ Write tests", "+ Тесты записи"))
        self._write_check.setToolTip(
            "Добавить тесты записи: Sequential Write + SLC Cache.\n"
            "⚠️ ДЕСТРУКТИВНО — все данные на диске будут уничтожены!"
        )
        self._write_check.setStyleSheet("color: #f38ba8;")

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(24)

        self._status = QLabel(tr("Select a drive", "Выберите диск"))
        self._status.setStyleSheet("color: #a6adc8;")

        controls.addWidget(self._btn_start)
        controls.addWidget(self._btn_stop)
        controls.addWidget(self._write_check)
        controls.addWidget(self._progress, stretch=1)
        controls.addWidget(self._status)
        layout.addLayout(controls)

        # --- Result cards ---
        results = QHBoxLayout()
        results.setSpacing(10)

        self._seq_card = _ResultCard(tr("Seq Read", "Послед. чтение"))
        self._seq_write_card = _ResultCard(tr("Seq Write", "Послед. запись"))
        self._rnd_card = _ResultCard(tr("4K Read", "4K чтение"))
        self._rnd_write_card = _ResultCard(tr("4K Write", "4K запись"))
        self._mixed_card = _ResultCard(tr("Mixed 70/30", "Микс 70/30"))
        self._verify_card = _ResultCard(tr("Verify", "Проверка"))
        self._slc_card = _ResultCard(tr("SLC Cache", "SLC кэш"))
        results.addWidget(self._seq_card)
        results.addWidget(self._seq_write_card)
        results.addWidget(self._rnd_card)
        results.addWidget(self._rnd_write_card)
        results.addWidget(self._mixed_card)
        results.addWidget(self._verify_card)
        results.addWidget(self._slc_card)
        layout.addLayout(results)

        # --- Charts (tabbed) ---
        from PySide6.QtWidgets import QTabWidget
        self._chart_tabs = QTabWidget()
        self._scatter = LatencyScatterWidget()
        self._histogram = LatencyHistogramWidget()
        self._sweep_chart = LineChartWidget("Drive Read Sweep", "Position (GB)", "MB/s")
        self._slc_chart = LineChartWidget("SLC Cache Write", "Written (GB)", "MB/s")
        self._chart_tabs.addTab(self._scatter, tr("Latency Scatter", "Задержки (точки)"))
        self._chart_tabs.addTab(self._histogram, tr("Latency Histogram", "Гистограмма задержек"))
        self._chart_tabs.addTab(self._sweep_chart, tr("Drive Sweep", "Чтение по позициям"))
        self._chart_tabs.addTab(self._slc_chart, "SLC кэш")
        layout.addWidget(self._chart_tabs, stretch=1)

    # --- Public API ---

    def set_drive(self, drive_number: int, capacity_bytes: int,
                  interface_type: str = "", model: str = ""):
        """Установить диск для бенчмарка."""
        self.stop()
        self._drive_number = drive_number
        self._interface_type = interface_type
        self._capacity_bytes = capacity_bytes
        self._model = model
        self._btn_start.setEnabled(True)
        self._status.setText(tr("Ready", "Готов"))
        self._status.setStyleSheet("color: #a6adc8;")
        self._clear_results()

    def clear(self):
        """Полный сброс панели."""
        self.stop()
        self._drive_number = None
        self._capacity_bytes = 0
        self._btn_start.setEnabled(False)
        self._status.setText(tr("Select a drive", "Выберите диск"))
        self._status.setStyleSheet("color: #a6adc8;")
        self._clear_results()

    def stop(self):
        """Остановить текущий бенчмарк (если запущен)."""
        if self._worker:
            self._worker.cancel()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._worker = None
        self._thread = None
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_stop.setEnabled(False)

    # --- Private ---

    def _clear_results(self):
        self._seq_card.clear()
        self._seq_write_card.clear()
        self._rnd_card.clear()
        self._rnd_write_card.clear()
        self._mixed_card.clear()
        self._verify_card.clear()
        self._slc_card.clear()
        self._scatter.clear()
        self._histogram.clear()
        self._sweep_chart.clear()
        self._slc_chart.clear()
        self._progress.setValue(0)

    def _start_benchmark(self):
        if self._drive_number is None:
            return

        include_write = self._write_check.isChecked()

        if include_write:
            size_gb = self._capacity_bytes / (1024 ** 3)
            reply = QMessageBox.warning(
                self, tr("Benchmark — Write Tests", "Бенчмарк — Тесты записи"),
                tr(
                    f"⚠️ Write tests will DESTROY ALL DATA on the disk!\n\n"
                    f"Disk: {self._model.strip()} ({size_gb:.1f} GB)\n\n"
                    f"Sequential Write: 512 MB\n"
                    f"Random 4K Write + Mixed I/O + Verify\n"
                    f"SLC Cache: up to 50 GB\n\n"
                    f"ARE YOU SURE?",
                    f"⚠️ Тесты записи УНИЧТОЖАТ ВСЕ ДАННЫЕ на диске!\n\n"
                    f"Диск: {self._model.strip()} ({size_gb:.1f} GB)\n\n"
                    f"Sequential Write: запись 512 MB\n"
                    f"Random 4K Write + Mixed I/O + Verify\n"
                    f"SLC Cache: запись до 50 GB\n\n"
                    f"ВЫ УВЕРЕНЫ?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            # Двойная защита для системного диска
            from ..core.winapi import is_system_drive
            if is_system_drive(self._drive_number):
                reply2 = QMessageBox.critical(
                    self, tr("⚠️ SYSTEM DISK!", "⚠️ СИСТЕМНЫЙ ДИСК!"),
                    tr(
                        f"THIS IS THE SYSTEM DISK (contains Windows)!\n"
                        f"Disk: {self._model.strip()}\n\n"
                        f"Write tests will make the computer unbootable!\n"
                        f"You will lose ALL programs and data!\n\n"
                        f"DO YOU REALLY WANT TO CONTINUE?",
                        f"ЭТО СИСТЕМНЫЙ ДИСК (содержит Windows)!\n"
                        f"Диск: {self._model.strip()}\n\n"
                        f"Тесты записи сделают компьютер незагружаемым!\n"
                        f"Вы потеряете ВСЕ программы и данные!\n\n"
                        f"ВЫ ДЕЙСТВИТЕЛЬНО ХОТИТЕ ПРОДОЛЖИТЬ?",
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply2 != QMessageBox.StandardButton.Yes:
                    return

        self._clear_results()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._write_check.setEnabled(False)
        self._status.setText(tr("Running...", "Выполняется..."))
        self._status.setStyleSheet("color: #cdd6f4;")

        self._worker = _BenchmarkWorker(self._drive_number, self._capacity_bytes,
                                        include_write, self._interface_type)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._thread.start()

    def _stop_benchmark(self):
        self.stop()
        self._status.setText(tr("Cancelled", "Отменено"))
        self._status.setStyleSheet("color: #f9e2af;")

    def _on_progress(self, phase: str, pct: float, message: str):
        if self._write_check.isChecked():
            phase_map = {
                "sequential": (0, 8, "Seq Read"),
                "random":     (8, 8, "4K Read"),
                "sweep":      (16, 14, tr("Drive Sweep", "Чтение по позициям")),
                "seq_write":  (30, 8, "Seq Write"),
                "rnd_write":  (38, 8, "4K Write"),
                "mixed":      (46, 8, "Mixed 70/30"),
                "verify":     (54, 10, "Verify"),
                "slc_cache":  (64, 36, "SLC кэш"),
            }
        else:
            phase_map = {
                "sequential": (0, 15, "Seq Read"),
                "random":     (15, 15, "4K Read"),
                "sweep":      (30, 70, tr("Drive Sweep", "Чтение по позициям")),
            }
        base, span, name = phase_map.get(phase, (0, 100, phase))
        overall = base + pct * span
        self._progress.setValue(int(overall))
        self._status.setText(f"{name}: {message}")

    def _on_finished(self, result: BenchmarkResult):
        self._last_result = result
        self._progress.setValue(100)
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._write_check.setEnabled(True)

        if result.io_errors:
            err_count = len(result.io_errors)
            self._status.setText(tr(f"Done with {err_count} I/O error(s)",
                                    f"Готово, {err_count} ошибок I/O"))
            self._status.setStyleSheet("color: #f38ba8;")
        else:
            self._status.setText(tr("Done!", "Готово!"))
            self._status.setStyleSheet("color: #a6e3a1;")

        # Sequential Read
        if result.sequential_speed_mbps > 0:
            mb = result.sequential_bytes_read / (1024 * 1024)
            self._seq_card.set_result(
                f"{result.sequential_speed_mbps:.1f} MB/s",
                f"{mb:.0f} MB read in {result.sequential_time_sec:.2f}s",
            )

        # Sequential Write
        if result.seq_write_speed_mbps > 0:
            mb = result.seq_write_bytes / (1024 * 1024)
            self._seq_write_card.set_result(
                f"{result.seq_write_speed_mbps:.1f} MB/s",
                f"{mb:.0f} MB written in {result.seq_write_time_sec:.2f}s",
            )

        # Random 4K Read
        if result.random_reads_count > 0:
            self._rnd_card.set_result(
                f"{result.random_iops:,.0f} IOPS",
                f"Avg: {result.random_avg_latency_us:.1f} μs\n"
                f"P95: {result.random_p95_latency_us:.0f}  P99: {result.random_p99_latency_us:.0f}\n"
                f"P99.9: {result.random_p999_latency_us:.0f}  P99.99: {result.random_p9999_latency_us:.0f} μs",
            )

        # Random 4K Write
        if result.random_write_count > 0:
            self._rnd_write_card.set_result(
                f"{result.random_write_iops:,.0f} IOPS",
                f"Avg: {result.random_write_avg_latency_us:.1f} μs",
            )

        # Mixed I/O
        if result.mixed_count > 0:
            self._mixed_card.set_result(
                f"{result.mixed_total_iops:,.0f} IOPS",
                f"R: {result.mixed_read_iops:,.0f}  /  "
                f"W: {result.mixed_write_iops:,.0f}",
            )

        # Write-Read-Verify
        if result.verify_blocks_tested > 0:
            if result.verify_blocks_failed == 0:
                self._verify_card.set_result(
                    "✓ ОК",
                    f"{result.verify_blocks_tested} blocks OK\n"
                    f"{result.verify_speed_mbps:.1f} MB/s",
                )
            else:
                self._verify_card.set_result(
                    f"✗ {result.verify_blocks_failed} FAIL",
                    f"{result.verify_blocks_ok} OK / "
                    f"{result.verify_blocks_failed} corrupted!",
                )

        # SLC Cache
        if result.slc_cache_size_gb > 0:
            self._slc_card.set_result(
                f"{result.slc_cache_size_gb:.1f} GB",
                f"SLC: {result.slc_speed_mbps:.0f} MB/s\n"
                f"Post-cache: {result.slc_post_cache_speed_mbps:.0f} MB/s",
            )
        elif result.slc_speed_mbps > 0:
            self._slc_card.set_result(
                tr("No cliff", "Без падения"),
                f"{tr('Speed', 'Скорость')}: {result.slc_speed_mbps:.0f} MB/s",
            )

        # Показать I/O Error в карточках write-тестов, если они не заполнились
        if result.io_errors:
            err_label = tr("I/O Error", "Ошибка I/O")
            phase_cards = {
                "seq_write": (self._seq_write_card, result.seq_write_speed_mbps),
                "rnd_write": (self._rnd_write_card, result.random_write_count),
                "mixed": (self._mixed_card, result.mixed_count),
                "verify": (self._verify_card, result.verify_blocks_tested),
                "slc_cache": (self._slc_card, result.slc_speed_mbps),
            }
            for err in result.io_errors:
                phase = err.split(":")[0]
                card_info = phase_cards.get(phase)
                if card_info and card_info[1] == 0:
                    card_info[0].set_result(f"✗ {err_label}", err.split(": ", 1)[-1][:60])

        # Charts
        if result.latency_points:
            self._scatter.set_points(result.latency_points)
            # Histogram from latencies
            lats = [lat for _, lat in result.latency_points]
            self._histogram.set_latencies(lats)
        if result.sweep_points:
            self._sweep_chart.set_points(result.sweep_points)
        if result.slc_points:
            self._slc_chart.set_points(result.slc_points)

    def _on_error(self, error_msg: str):
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_stop.setEnabled(False)
        self._write_check.setEnabled(True)
        self._status.setText(f"{tr("Error", "Ошибка")}: {error_msg}")
        self._status.setStyleSheet("color: #f38ba8;")
