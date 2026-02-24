"""Панель сканирования поверхности: блочная карта + статистика + управление.

Визуализация в стиле Victoria HDD — сетка цветных прямоугольников,
заполняемая в реальном времени при последовательном чтении диска.
"""

import logging
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QProgressBar, QLabel, QGroupBox, QSplitter, QComboBox,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QRectF
from PySide6.QtGui import QFont, QPainter, QColor, QPen

from ..core.surface_scan import SurfaceScanEngine, BLOCK_SIZES, DEFAULT_BLOCK_SIZE
from ..core.models import BlockCategory, SurfaceScanResult

logger = logging.getLogger(__name__)

# Цвета категорий (Catppuccin Mocha)
CATEGORY_COLORS = {
    BlockCategory.PENDING:    QColor(49, 50, 68),     # surface0
    BlockCategory.EXCELLENT:  QColor(88, 91, 112),     # surface2
    BlockCategory.GOOD:       QColor(166, 227, 161),   # green
    BlockCategory.ACCEPTABLE: QColor(148, 226, 213),   # teal
    BlockCategory.SLOW:       QColor(249, 226, 175),   # yellow
    BlockCategory.VERY_SLOW:  QColor(250, 179, 135),   # peach
    BlockCategory.CRITICAL:   QColor(243, 139, 168),   # red
    BlockCategory.ERROR:      QColor(235, 160, 172),   # maroon
}

CATEGORY_LABELS = {
    BlockCategory.EXCELLENT:  "< 5 ms",
    BlockCategory.GOOD:       "< 20 ms",
    BlockCategory.ACCEPTABLE: "< 50 ms",
    BlockCategory.SLOW:       "< 150 ms",
    BlockCategory.VERY_SLOW:  "< 500 ms",
    BlockCategory.CRITICAL:   "\u2265 500 ms",
    BlockCategory.ERROR:      "Errors",
}


# ---------------------------------------------------------------------------
#  Worker
# ---------------------------------------------------------------------------

class _SurfaceScanWorker(QObject):
    """Фоновый воркер: запускает SurfaceScanEngine и шлёт сигналы в GUI."""

    block_scanned = Signal(int, int, float)   # block_index, category_value, latency_ms
    progress = Signal(float, str)             # 0..1, message
    finished = Signal(object)                 # SurfaceScanResult
    error = Signal(str)

    def __init__(self, drive_number: int, capacity_bytes: int,
                 block_size: int = DEFAULT_BLOCK_SIZE):
        super().__init__()
        self._engine = SurfaceScanEngine(drive_number, capacity_bytes, block_size)

    @property
    def total_blocks(self) -> int:
        return self._engine.total_blocks

    def run(self):
        try:
            result = self._engine.scan(
                block_callback=lambda idx, cat, lat: self.block_scanned.emit(idx, cat.value, lat),
                progress_callback=lambda pct, msg: self.progress.emit(pct, msg),
            )
            self.finished.emit(result)
        except Exception as e:
            logger.exception("Surface scan error")
            self.error.emit(str(e))

    def cancel(self):
        self._engine.cancel()


# ---------------------------------------------------------------------------
#  Block Map Widget (custom QPainter)
# ---------------------------------------------------------------------------

class BlockMapWidget(QWidget):
    """Сетка цветных прямоугольников — визуализация поверхности диска.

    Каждая ячейка представляет один или несколько блоков диска.
    Цвет = категория времени отклика. Заполняется слева направо, сверху вниз.
    """

    CELL_SIZE = 12   # пикселей
    CELL_GAP = 1     # зазор между ячейками

    def __init__(self, parent=None):
        super().__init__(parent)
        self._total_blocks = 0
        self._block_categories: list[int] = []  # BlockCategory.value per block
        self._dirty = False
        self.setMinimumSize(200, 200)

    def reset(self, total_blocks: int):
        """Сброс карты для нового сканирования."""
        self._total_blocks = total_blocks
        self._block_categories = [BlockCategory.PENDING.value] * total_blocks
        self._dirty = True
        self.update()

    def clear(self):
        """Полный сброс."""
        self._total_blocks = 0
        self._block_categories.clear()
        self._dirty = True
        self.update()

    def set_block(self, block_index: int, category_value: int):
        """Обновить категорию одного блока."""
        if 0 <= block_index < len(self._block_categories):
            self._block_categories[block_index] = category_value
            self._dirty = True

    def flush(self):
        """Перерисовать виджет (вызывается по таймеру)."""
        if self._dirty:
            self._dirty = False
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(30, 30, 46))  # base

        if not self._total_blocks or not self._block_categories:
            painter.setPen(QColor(88, 91, 112))
            font = QFont("Segoe UI", 11)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             "Select a drive and start scan")
            painter.end()
            return

        step = self.CELL_SIZE + self.CELL_GAP
        cols = max(1, rect.width() // step)
        rows = max(1, rect.height() // step)
        cells = cols * rows

        # Сколько физических блоков на одну ячейку
        blocks_per_cell = max(1, (self._total_blocks + cells - 1) // cells)

        # Предварительно создадим QColor для каждой категории
        cat_colors = {}
        for cat in BlockCategory:
            cat_colors[cat.value] = CATEGORY_COLORS[cat]

        painter.setPen(Qt.PenStyle.NoPen)

        for cell_idx in range(cells):
            col = cell_idx % cols
            row = cell_idx // cols

            # Определяем категорию ячейки (худшая из блоков в группе)
            start_block = cell_idx * blocks_per_cell
            if start_block >= self._total_blocks:
                break

            end_block = min(start_block + blocks_per_cell, self._total_blocks)
            worst = BlockCategory.PENDING.value
            for bi in range(start_block, end_block):
                val = self._block_categories[bi]
                if val > worst:
                    worst = val

            color = cat_colors.get(worst, cat_colors[BlockCategory.PENDING.value])
            x = col * step
            y = row * step

            painter.setBrush(color)
            painter.drawRect(x, y, self.CELL_SIZE, self.CELL_SIZE)

            # Метка X для ошибочных блоков
            if worst == BlockCategory.ERROR.value:
                painter.setPen(QPen(QColor(30, 30, 46), 1.5))
                painter.drawLine(x + 2, y + 2, x + self.CELL_SIZE - 2, y + self.CELL_SIZE - 2)
                painter.drawLine(x + self.CELL_SIZE - 2, y + 2, x + 2, y + self.CELL_SIZE - 2)
                painter.setPen(Qt.PenStyle.NoPen)

        painter.end()


# ---------------------------------------------------------------------------
#  Statistics Panel
# ---------------------------------------------------------------------------

class _StatsPanel(QGroupBox):
    """Панель статистики: легенда + счётчики + скорость/время."""

    def __init__(self, parent=None):
        super().__init__("Statistics", parent)
        self.setMinimumWidth(180)
        self.setMaximumWidth(220)

        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        # Легенда категорий
        self._cat_labels: dict[int, QLabel] = {}
        for cat in CATEGORY_LABELS:
            row = QHBoxLayout()
            row.setSpacing(6)

            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            color = CATEGORY_COLORS[cat]
            swatch.setStyleSheet(
                f"background-color: {color.name()}; border-radius: 2px;"
            )

            name = QLabel(CATEGORY_LABELS[cat])
            name.setFont(QFont("Segoe UI", 10))
            name.setStyleSheet("color: #cdd6f4;")

            count_label = QLabel("0")
            count_label.setFont(QFont("Consolas", 10))
            count_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            count_label.setStyleSheet("color: #a6adc8;")
            self._cat_labels[cat.value] = count_label

            row.addWidget(swatch)
            row.addWidget(name, stretch=1)
            row.addWidget(count_label)
            layout.addLayout(row)

        # Разделитель
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #313244;")
        layout.addWidget(sep)

        # Скорость, время, ETA
        info_font = QFont("Segoe UI", 10)

        self._speed_label = QLabel("Speed: —")
        self._speed_label.setFont(info_font)
        self._speed_label.setStyleSheet("color: #a6adc8;")

        self._time_label = QLabel("Time: —")
        self._time_label.setFont(info_font)
        self._time_label.setStyleSheet("color: #a6adc8;")

        self._eta_label = QLabel("ETA: —")
        self._eta_label.setFont(info_font)
        self._eta_label.setStyleSheet("color: #a6adc8;")

        self._scanned_label = QLabel("Scanned: —")
        self._scanned_label.setFont(info_font)
        self._scanned_label.setStyleSheet("color: #a6adc8;")

        layout.addWidget(self._speed_label)
        layout.addWidget(self._time_label)
        layout.addWidget(self._eta_label)
        layout.addWidget(self._scanned_label)

        layout.addStretch()

    def update_counts(self, counts: dict[int, int]):
        for cat_val, label in self._cat_labels.items():
            label.setText(f"{counts.get(cat_val, 0):,}")

    def update_info(self, speed_mbps: float, elapsed_sec: float, eta_sec: float,
                    scanned: int, total: int):
        self._speed_label.setText(f"Speed: {speed_mbps:.1f} MB/s")
        self._time_label.setText(f"Time: {self._fmt_time(elapsed_sec)}")
        if eta_sec > 0:
            self._eta_label.setText(f"ETA: {self._fmt_time(eta_sec)}")
        else:
            self._eta_label.setText("ETA: —")
        pct = scanned / total * 100 if total > 0 else 0
        self._scanned_label.setText(f"Scanned: {scanned:,} / {total:,} ({pct:.1f}%)")

    def clear(self):
        for label in self._cat_labels.values():
            label.setText("0")
        self._speed_label.setText("Speed: —")
        self._time_label.setText("Time: —")
        self._eta_label.setText("ETA: —")
        self._scanned_label.setText("Scanned: —")

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
#  Surface Scan Panel
# ---------------------------------------------------------------------------

class SurfaceScanPanel(QWidget):
    """Панель сканирования поверхности: карта блоков + статистика + управление."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drive_number: int | None = None
        self._capacity_bytes: int = 0
        self._worker: _SurfaceScanWorker | None = None
        self._thread: QThread | None = None

        # Для расчёта скорости и ETA
        self._scan_start_time: float = 0.0
        self._current_block_size: int = DEFAULT_BLOCK_SIZE
        self._counts: dict[int, int] = {}

        self._setup_ui()

        # Таймер для обновления карты (30 fps)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(33)  # ~30 fps
        self._refresh_timer.timeout.connect(self._on_refresh_tick)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # --- Controls ---
        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._btn_start = QPushButton("\u25b6  Start Scan")
        self._btn_start.setFixedHeight(36)
        self._btn_start.setEnabled(False)
        self._btn_start.clicked.connect(self._start_scan)

        self._btn_stop = QPushButton("\u25a0  Stop")
        self._btn_stop.setFixedHeight(36)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_scan)

        # Выбор размера блока
        block_label = QLabel("Block:")
        block_label.setStyleSheet("color: #a6adc8;")
        self._block_combo = QComboBox()
        self._block_combo.setFixedHeight(36)
        self._block_combo.setMinimumWidth(90)
        default_idx = 0
        for i, (label, size) in enumerate(BLOCK_SIZES):
            self._block_combo.addItem(label, size)
            if size == DEFAULT_BLOCK_SIZE:
                default_idx = i
        self._block_combo.setCurrentIndex(default_idx)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)  # 0.1% точность
        self._progress.setValue(0)
        self._progress.setFixedHeight(24)

        self._status = QLabel("Select a drive")
        self._status.setStyleSheet("color: #a6adc8;")

        controls.addWidget(self._btn_start)
        controls.addWidget(self._btn_stop)
        controls.addWidget(block_label)
        controls.addWidget(self._block_combo)
        controls.addWidget(self._progress, stretch=1)
        controls.addWidget(self._status)
        layout.addLayout(controls)

        # --- Main area: Block Map (left) + Stats (right) ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self._block_map = BlockMapWidget()
        self._stats = _StatsPanel()

        splitter.addWidget(self._block_map)
        splitter.addWidget(self._stats)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 0)

        layout.addWidget(splitter, stretch=1)

    # --- Public API ---

    def set_drive(self, drive_number: int, capacity_bytes: int):
        """Установить диск для сканирования."""
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
        """Остановить текущее сканирование."""
        self._refresh_timer.stop()
        if self._worker:
            self._worker.cancel()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._worker = None
        self._thread = None
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_stop.setEnabled(False)
        self._block_combo.setEnabled(True)

    # --- Private ---

    def _clear_results(self):
        self._block_map.clear()
        self._stats.clear()
        self._progress.setValue(0)
        self._counts = {}

    def _start_scan(self):
        if self._drive_number is None:
            return

        self._clear_results()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._block_combo.setEnabled(False)
        self._status.setText("Scanning...")
        self._status.setStyleSheet("color: #cdd6f4;")

        block_size = self._block_combo.currentData()
        self._current_block_size = block_size
        self._worker = _SurfaceScanWorker(
            self._drive_number, self._capacity_bytes, block_size
        )

        # Инициализация карты
        self._block_map.reset(self._worker.total_blocks)
        self._counts = {cat.value: 0 for cat in BlockCategory if cat != BlockCategory.PENDING}
        self._scan_start_time = time.perf_counter()

        self._thread = QThread()
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.block_scanned.connect(self._on_block_scanned)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._refresh_timer.start()
        self._thread.start()

    def _stop_scan(self):
        self.stop()
        self._status.setText("Cancelled")
        self._status.setStyleSheet("color: #f9e2af;")

    def _on_block_scanned(self, block_index: int, category_value: int, latency_ms: float):
        """Обновление данных карты (вызывается из worker thread через signal)."""
        self._block_map.set_block(block_index, category_value)
        self._counts[category_value] = self._counts.get(category_value, 0) + 1

    def _on_refresh_tick(self):
        """Обновление GUI по таймеру (~30 fps)."""
        self._block_map.flush()
        self._stats.update_counts(self._counts)

        # Скорость и ETA
        if self._worker and self._scan_start_time > 0:
            elapsed = time.perf_counter() - self._scan_start_time
            total = self._worker.total_blocks
            scanned = sum(self._counts.values())
            speed_mbps = (scanned * self._current_block_size) / (1024 * 1024) / elapsed if elapsed > 0 else 0
            remaining = total - scanned
            eta_sec = remaining * (elapsed / scanned) if scanned > 0 else 0

            self._stats.update_info(speed_mbps, elapsed, eta_sec, scanned, total)

    def _on_progress(self, fraction: float, message: str):
        self._progress.setValue(int(fraction * 1000))
        self._status.setText(message)

    def _on_finished(self, result: SurfaceScanResult):
        self._refresh_timer.stop()
        self._block_map.flush()

        self._progress.setValue(1000)
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)

        # Итоговая статистика
        self._stats.update_counts(result.counts)
        self._stats.update_info(
            result.avg_speed_mbps, result.elapsed_sec, 0,
            result.scanned_blocks, result.total_blocks,
        )

        if result.error_count > 0:
            self._status.setText(
                f"Done — {result.error_count} error(s) found!"
            )
            self._status.setStyleSheet("color: #f38ba8;")
        else:
            self._status.setText("Done — no errors!")
            self._status.setStyleSheet("color: #a6e3a1;")

    def _on_error(self, error_msg: str):
        self._refresh_timer.stop()
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_stop.setEnabled(False)
        self._status.setText(f"Error: {error_msg}")
        self._status.setStyleSheet("color: #f38ba8;")
