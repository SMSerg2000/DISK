"""Панель бенчмарка: последовательное чтение, случайное 4K, scatter plot латентности."""

import logging

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QProgressBar, QLabel,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QRectF, QPointF
from PySide6.QtGui import QFont, QPainter, QColor, QBrush, QPen

from ..core.benchmark import BenchmarkEngine
from ..core.models import BenchmarkResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Worker (QThread)
# ---------------------------------------------------------------------------

class _BenchmarkWorker(QObject):
    """Фоновый воркер: запускает BenchmarkEngine и шлёт сигналы в GUI."""

    progress = Signal(str, float, str)   # phase, 0..1, message
    finished = Signal(object)            # BenchmarkResult
    error = Signal(str)

    def __init__(self, drive_number: int, capacity_bytes: int):
        super().__init__()
        self._engine = BenchmarkEngine(drive_number, capacity_bytes)

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
            "Disk Position (GB)",
        )

        # --- No data ---
        if not self._points:
            painter.setPen(QColor(88, 91, 112))
            font.setPointSize(11)
            painter.setFont(font)
            painter.drawText(
                plot,
                Qt.AlignmentFlag.AlignCenter,
                "Run benchmark to see latency distribution",
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

        self._btn_start = QPushButton("▶  Start Benchmark")
        self._btn_start.setFixedHeight(36)
        self._btn_start.setEnabled(False)
        self._btn_start.clicked.connect(self._start_benchmark)

        self._btn_stop = QPushButton("■  Stop")
        self._btn_stop.setFixedHeight(36)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_benchmark)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(24)

        self._status = QLabel("Select a drive")
        self._status.setStyleSheet("color: #a6adc8;")

        controls.addWidget(self._btn_start)
        controls.addWidget(self._btn_stop)
        controls.addWidget(self._progress, stretch=1)
        controls.addWidget(self._status)
        layout.addLayout(controls)

        # --- Result cards ---
        results = QHBoxLayout()
        results.setSpacing(10)

        self._seq_card = _ResultCard("Sequential Read")
        self._rnd_card = _ResultCard("Random 4K Read")
        results.addWidget(self._seq_card)
        results.addWidget(self._rnd_card)
        layout.addLayout(results)

        # --- Scatter plot ---
        self._scatter = LatencyScatterWidget()
        layout.addWidget(self._scatter, stretch=1)

    # --- Public API ---

    def set_drive(self, drive_number: int, capacity_bytes: int):
        """Установить диск для бенчмарка."""
        self.stop()
        self._drive_number = drive_number
        self._capacity_bytes = capacity_bytes
        self._btn_start.setEnabled(True)
        self._status.setText("Ready")
        self._status.setStyleSheet("color: #a6adc8;")
        self._clear_results()

    def clear(self):
        """Полный сброс панели."""
        self.stop()
        self._drive_number = None
        self._capacity_bytes = 0
        self._btn_start.setEnabled(False)
        self._status.setText("Select a drive")
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
        self._rnd_card.clear()
        self._scatter.clear()
        self._progress.setValue(0)

    def _start_benchmark(self):
        if self._drive_number is None:
            return

        self._clear_results()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._status.setText("Running...")
        self._status.setStyleSheet("color: #cdd6f4;")

        self._worker = _BenchmarkWorker(self._drive_number, self._capacity_bytes)
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
        self._status.setText("Cancelled")
        self._status.setStyleSheet("color: #f9e2af;")

    def _on_progress(self, phase: str, pct: float, message: str):
        # Sequential = 0..50%, Random = 50..100%
        overall = pct * 50 if phase == "sequential" else 50 + pct * 50
        self._progress.setValue(int(overall))

        phase_name = "Sequential" if phase == "sequential" else "Random 4K"
        self._status.setText(f"{phase_name}: {message}")

    def _on_finished(self, result: BenchmarkResult):
        self._progress.setValue(100)
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._status.setText("Done!")
        self._status.setStyleSheet("color: #a6e3a1;")

        # Sequential
        if result.sequential_speed_mbps > 0:
            mb = result.sequential_bytes_read / (1024 * 1024)
            self._seq_card.set_result(
                f"{result.sequential_speed_mbps:.1f} MB/s",
                f"{mb:.0f} MB read in {result.sequential_time_sec:.2f}s",
            )

        # Random 4K
        if result.random_reads_count > 0:
            self._rnd_card.set_result(
                f"{result.random_iops:,.0f} IOPS",
                f"Avg: {result.random_avg_latency_us:.1f} μs\n"
                f"Min: {result.random_min_latency_us:.1f} μs  /  "
                f"Max: {result.random_max_latency_us:.1f} μs",
            )

        # Scatter
        if result.latency_points:
            self._scatter.set_points(result.latency_points)

    def _on_error(self, error_msg: str):
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_stop.setEnabled(False)
        self._status.setText(f"Error: {error_msg}")
        self._status.setStyleSheet("color: #f38ba8;")
