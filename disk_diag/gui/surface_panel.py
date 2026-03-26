"""Панель сканирования поверхности: блочная карта + статистика + управление.

Визуализация в стиле Victoria HDD — сетка цветных прямоугольников,
заполняемая в реальном времени при последовательном чтении диска.
"""

import logging
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QProgressBar, QLabel, QGroupBox, QSplitter, QComboBox,
    QMessageBox, QCheckBox, QLineEdit, QTextEdit,
)
from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer, QRectF
from PySide6.QtGui import QFont, QPainter, QColor, QPen

from ..core.surface_scan import SurfaceScanEngine, BLOCK_SIZES, DEFAULT_BLOCK_SIZE
from ..core.models import BlockCategory, ScanMode, SurfaceScanResult
from ..i18n import tr

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
    BlockCategory.ERROR:      tr("Errors", "Ошибки"),
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
    bad_sector = Signal(int)                  # LBA битого сектора (реалтайм)

    def __init__(self, drive_number: int, capacity_bytes: int,
                 block_size: int = DEFAULT_BLOCK_SIZE,
                 mode: ScanMode = ScanMode.IGNORE,
                 erase_slow: bool = False,
                 start_offset: int = 0,
                 end_offset: int = 0):
        super().__init__()
        self._engine = SurfaceScanEngine(
            drive_number, capacity_bytes, block_size, mode, erase_slow,
            start_offset, end_offset,
        )

    @property
    def total_blocks(self) -> int:
        return self._engine.total_blocks

    def run(self):
        try:
            result = self._engine.scan(
                block_callback=lambda idx, cat, lat: self.block_scanned.emit(idx, cat.value, lat),
                progress_callback=lambda pct, msg: self.progress.emit(pct, msg),
                bad_sector_callback=lambda lba: self.bad_sector.emit(lba),
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
                             tr("Select a drive and start scan", "Выберите диск и запустите сканирование"))
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
        super().__init__(tr("Statistics", "Статистика"), parent)
        self.setMinimumWidth(220)
        self.setMaximumWidth(320)

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

        self._speed_label = QLabel(tr("Speed: —", "Скорость: —"))
        self._speed_label.setFont(info_font)
        self._speed_label.setStyleSheet("color: #a6adc8;")

        self._time_label = QLabel(tr("Time: —", "Время: —"))
        self._time_label.setFont(info_font)
        self._time_label.setStyleSheet("color: #a6adc8;")

        self._eta_label = QLabel(tr("ETA: —", "Осталось: —"))
        self._eta_label.setFont(info_font)
        self._eta_label.setStyleSheet("color: #a6adc8;")

        self._scanned_label = QLabel(tr("Scanned: —", "Просканировано: —"))
        self._scanned_label.setFont(info_font)
        self._scanned_label.setStyleSheet("color: #a6adc8;")

        self._repaired_label = QLabel("")
        self._repaired_label.setFont(info_font)
        self._repaired_label.setStyleSheet("color: #a6e3a1;")
        self._repaired_label.hide()

        layout.addWidget(self._speed_label)
        layout.addWidget(self._time_label)
        layout.addWidget(self._eta_label)
        layout.addWidget(self._scanned_label)
        layout.addWidget(self._repaired_label)

        # Список битых секторов (LBA) — скроллируемый
        self._bad_sectors_edit = QTextEdit()
        self._bad_sectors_edit.setReadOnly(True)
        self._bad_sectors_edit.setFont(QFont("Consolas", 9))
        self._bad_sectors_edit.setStyleSheet(
            "color: #f38ba8; background-color: #1e1e2e; border: none;"
        )
        self._bad_sectors_edit.hide()
        layout.addWidget(self._bad_sectors_edit, stretch=1)

    def update_counts(self, counts: dict[int, int]):
        for cat_val, label in self._cat_labels.items():
            label.setText(f"{counts.get(cat_val, 0):,}")

    def update_info(self, speed_mbps: float, elapsed_sec: float, eta_sec: float,
                    scanned: int, total: int, block_size: int = 0,
                    start_lba: int = 0):
        self._speed_label.setText(f"{tr("Speed", "Скорость")}: {speed_mbps:.1f} MB/s")
        self._time_label.setText(f"{tr("Time", "Время")}: {self._fmt_time(elapsed_sec)}")
        if eta_sec > 0:
            self._eta_label.setText(f"{tr("ETA", "Осталось")}: {self._fmt_time(eta_sec)}")
        else:
            self._eta_label.setText(tr("ETA: —", "Осталось: —"))
        pct = scanned / total * 100 if total > 0 else 0
        if block_size > 0:
            # Абсолютная позиция в LBA (512-byte sectors)
            sectors_per_block = block_size // 512
            current_lba = start_lba + scanned * sectors_per_block
            end_lba = start_lba + total * sectors_per_block
            self._scanned_label.setText(
                f"Scanned: LBA {current_lba:,} / {end_lba:,} ({pct:.1f}%)"
            )
        else:
            self._scanned_label.setText(f"Scanned: {scanned:,} / {total:,} ({pct:.1f}%)")

    def update_repair_stats(self, repaired: int, write_errors: int):
        if repaired > 0 or write_errors > 0:
            lines = []
            if repaired > 0:
                lines.append(f"{tr('Repaired', 'Исправлено')}: {repaired:,}")
            if write_errors > 0:
                lines.append(f"{tr('Write err', 'Ошибок записи')}: {write_errors:,}")
            self._repaired_label.setText("\n".join(lines))
            self._repaired_label.show()
        else:
            self._repaired_label.hide()

    def update_bad_sectors(self, lbas: list[int]):
        if not lbas:
            self._bad_sectors_edit.hide()
            return
        header = f"Битые секторы ({len(lbas):,}):"
        lines = [f"  LBA {lba:,}" for lba in lbas]
        self._bad_sectors_edit.setText(header + "\n" + "\n".join(lines))
        # Автоскролл вниз к последнему найденному
        scrollbar = self._bad_sectors_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self._bad_sectors_edit.show()

    def clear(self):
        for label in self._cat_labels.values():
            label.setText("0")
        self._speed_label.setText(tr("Speed: —", "Скорость: —"))
        self._time_label.setText(tr("Time: —", "Время: —"))
        self._eta_label.setText(tr("ETA: —", "Осталось: —"))
        self._scanned_label.setText(tr("Scanned: —", "Просканировано: —"))
        self._repaired_label.hide()
        self._bad_sectors_edit.clear()
        self._bad_sectors_edit.hide()

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
        self._model: str = ""

        # Для расчёта скорости и ETA
        self._scan_start_time: float = 0.0
        self._current_block_size: int = DEFAULT_BLOCK_SIZE
        self._start_lba: int = 0
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
        block_label = QLabel(tr("Block:", "Блок:"))
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

        # Выбор режима сканирования
        mode_label = QLabel(tr("Mode:", "Режим:"))
        mode_label.setStyleSheet("color: #a6adc8;")
        self._mode_combo = QComboBox()
        self._mode_combo.setFixedHeight(36)
        self._mode_combo.setMinimumWidth(100)
        self._mode_combo.addItem("Ignore", ScanMode.IGNORE)
        self._mode_combo.addItem("Erase", ScanMode.ERASE)
        self._mode_combo.addItem("Refresh", ScanMode.REFRESH)
        self._mode_combo.addItem("WRITE !!!", ScanMode.WRITE)
        self._mode_combo.setToolTip(
            "Ignore — только чтение\n"
            "Erase — запись нулей в нечитаемые секторы (firmware HDD переназначит их)\n"
            "Refresh — чтение → перезапись тех же данных (освежает секторы)\n"
            "Write — ПОЛНОЕ СТИРАНИЕ: запись нулей на всю поверхность (все данные будут уничтожены!)"
        )
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        # Чекбокс "включая медленные" — виден только при Erase
        self._slow_check = QCheckBox("+ Slow")
        self._slow_check.setToolTip(
            "Стирать также медленные секторы (VERY_SLOW ≥150ms, CRITICAL ≥500ms).\n"
            "Данные в этих секторах будут уничтожены!"
        )
        self._slow_check.setStyleSheet("color: #f9e2af;")
        self._slow_check.hide()

        # Диапазон сканирования (LBA)
        from_label = QLabel("LBA from:")
        from_label.setStyleSheet("color: #a6adc8;")
        self._from_edit = QLineEdit("0")
        self._from_edit.setFixedHeight(36)
        self._from_edit.setFixedWidth(120)
        self._from_edit.setPlaceholderText("Start LBA")
        self._from_edit.setToolTip("Начальный сектор (LBA)")

        to_label = QLabel("to:")
        to_label.setStyleSheet("color: #a6adc8;")
        self._to_edit = QLineEdit("0")
        self._to_edit.setFixedHeight(36)
        self._to_edit.setFixedWidth(120)
        self._to_edit.setPlaceholderText("End LBA")
        self._to_edit.setToolTip("Конечный сектор (LBA)")

        self._from_edit.textChanged.connect(lambda: self._update_range_hint())
        self._to_edit.textChanged.connect(lambda: self._update_range_hint())

        self._range_hint = QLabel("")
        self._range_hint.setStyleSheet("color: #585b70; font-size: 10px;")

        self._progress = QProgressBar()
        self._progress.setRange(0, 1000)  # 0.1% точность
        self._progress.setValue(0)
        self._progress.setFixedHeight(24)

        self._status = QLabel(tr("Select a drive", "Выберите диск"))
        self._status.setStyleSheet("color: #a6adc8;")

        controls.addWidget(self._btn_start)
        controls.addWidget(self._btn_stop)
        controls.addWidget(block_label)
        controls.addWidget(self._block_combo)
        controls.addWidget(mode_label)
        controls.addWidget(self._mode_combo)
        controls.addWidget(self._slow_check)
        controls.addWidget(from_label)
        controls.addWidget(self._from_edit)
        controls.addWidget(to_label)
        controls.addWidget(self._to_edit)
        controls.addWidget(self._range_hint)
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

    def set_drive(self, drive_number: int, capacity_bytes: int, model: str = ""):
        """Установить диск для сканирования."""
        self.stop()
        self._drive_number = drive_number
        self._capacity_bytes = capacity_bytes
        self._model = model
        self._btn_start.setEnabled(True)
        self._status.setText(tr("Ready", "Готов"))
        self._status.setStyleSheet("color: #a6adc8;")
        self._clear_results()

        # Обновляем диапазон (LBA)
        max_lba = capacity_bytes // 512
        self._from_edit.setText("0")
        self._to_edit.setText(str(max_lba))
        self._update_range_hint()

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
        self._mode_combo.setEnabled(True)
        self._slow_check.setEnabled(True)
        self._from_edit.setEnabled(True)
        self._to_edit.setEnabled(True)

    def _on_mode_changed(self, index: int):
        """Показать/скрыть чекбокс + Slow при переключении режима."""
        mode = self._mode_combo.currentData()
        self._slow_check.setVisible(mode == ScanMode.ERASE)

    def _update_range_hint(self):
        """Обновить подсказку с размером диапазона в GB."""
        try:
            start_lba = int(self._from_edit.text().replace(",", "").strip() or "0")
            end_lba = int(self._to_edit.text().replace(",", "").strip() or "0")
            if end_lba > start_lba:
                size_gb = (end_lba - start_lba) * 512 / (1024 ** 3)
                self._range_hint.setText(f"({size_gb:.1f} GB)")
            else:
                self._range_hint.setText("")
        except ValueError:
            self._range_hint.setText("")

    # --- Private ---

    def _clear_results(self):
        self._block_map.clear()
        self._stats.clear()
        self._progress.setValue(0)
        self._counts = {}
        self._bad_lbas: list[int] = []

    def _start_scan(self):
        if self._drive_number is None:
            return

        mode = self._mode_combo.currentData()

        erase_slow = self._slow_check.isChecked() and mode == ScanMode.ERASE

        # Предупреждение для деструктивных режимов
        if mode != ScanMode.IGNORE:
            if mode == ScanMode.WRITE:
                size_gb = self._capacity_bytes / (1024 ** 3)
                warn_text = tr(
                    f"⚠️ WRITE MODE — FULL SURFACE ERASE!\n\n"
                    f"Disk: {self._model.strip()} ({size_gb:.1f} GB)\n\n"
                    f"Writing zeros to EVERY sector.\n"
                    f"ALL DATA WILL BE PERMANENTLY DESTROYED!\n"
                    f"All bad sectors will be remapped by firmware.\n\n"
                    f"ARE YOU SURE?",
                    f"⚠️ РЕЖИМ WRITE — ПОЛНОЕ СТИРАНИЕ ПОВЕРХНОСТИ!\n\n"
                    f"Диск: {self._model.strip()} ({size_gb:.1f} GB)\n\n"
                    f"Запись нулей на КАЖДЫЙ сектор диска.\n"
                    f"ВСЕ ДАННЫЕ БУДУТ БЕЗВОЗВРАТНО УНИЧТОЖЕНЫ!\n"
                    f"Все бэд-секторы будут переназначены firmware.\n\n"
                    f"ВЫ УВЕРЕНЫ?",
                )
            elif mode == ScanMode.REFRESH:
                warn_text = tr(
                    "REFRESH mode rewrites ALL sectors.\n"
                    "Data is preserved (read → write same data),\n"
                    "but power failure during write may cause data loss.\n\n"
                    "Continue?",
                    "Режим REFRESH перезаписывает ВСЕ секторы диска.\n"
                    "Данные сохраняются (чтение → запись тех же данных),\n"
                    "но при сбое питания во время записи данные могут быть потеряны.\n\n"
                    "Продолжить?",
                )
            elif erase_slow:
                warn_text = tr(
                    "ERASE + Slow writes ZEROS to unreadable\n"
                    "AND slow sectors (≥150ms).\n"
                    "DATA IN THESE SECTORS WILL BE DESTROYED!\n\n"
                    "Continue?",
                    "Режим ERASE + Slow записывает НУЛИ в нечитаемые\n"
                    "И медленные секторы (≥150ms).\n"
                    "ДАННЫЕ В ЭТИХ СЕКТОРАХ БУДУТ УНИЧТОЖЕНЫ!\n\n"
                    "Продолжить?",
                )
            else:
                warn_text = tr(
                    "ERASE mode writes zeros to unreadable sectors.\n"
                    "HDD firmware will remap them from spare area.\n"
                    "Data in these sectors is already lost (unreadable).\n\n"
                    "Continue?",
                    "Режим ERASE записывает нули в нечитаемые секторы.\n"
                    "Firmware HDD переназначит их из резервной области.\n"
                    "Данные в этих секторах уже потеряны (не читаются).\n\n"
                    "Продолжить?",
                )

            reply = QMessageBox.warning(
                self, f"Surface Scan — {mode.value.upper()}",
                warn_text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

            # Двойная защита для системного диска
            if mode == ScanMode.WRITE:
                from ..core.winapi import is_system_drive
                if is_system_drive(self._drive_number):
                    reply2 = QMessageBox.critical(
                        self, tr("⚠️ SYSTEM DISK!", "⚠️ СИСТЕМНЫЙ ДИСК!"),
                        tr(
                            f"THIS IS THE SYSTEM DISK (contains Windows)!\n"
                            f"Disk: {self._model.strip()}\n\n"
                            f"Erasing will make the computer unbootable!\n"
                            f"You will lose ALL programs and data!\n\n"
                            f"DO YOU REALLY WANT TO ERASE THE SYSTEM DISK?",
                            f"ЭТО СИСТЕМНЫЙ ДИСК (содержит Windows)!\n"
                            f"Диск: {self._model.strip()}\n\n"
                            f"Стирание сделает компьютер незагружаемым!\n"
                            f"Вы потеряете ВСЕ программы и данные!\n\n"
                            f"ВЫ ДЕЙСТВИТЕЛЬНО ХОТИТЕ СТЕРЕТЬ СИСТЕМНЫЙ ДИСК?",
                        ),
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply2 != QMessageBox.StandardButton.Yes:
                        return

        self._clear_results()
        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._block_combo.setEnabled(False)
        self._mode_combo.setEnabled(False)
        self._slow_check.setEnabled(False)
        self._from_edit.setEnabled(False)
        self._to_edit.setEnabled(False)

        mode_label = mode.value
        if erase_slow:
            mode_label += "+slow"
        self._status.setText(f"Scanning ({mode_label})...")
        self._status.setStyleSheet("color: #cdd6f4;")

        block_size = self._block_combo.currentData()
        self._current_block_size = block_size

        # Парсим LBA из текстовых полей
        try:
            start_lba = int(self._from_edit.text().replace(",", "").strip() or "0")
        except ValueError:
            start_lba = 0
        try:
            end_lba = int(self._to_edit.text().replace(",", "").strip() or "0")
        except ValueError:
            end_lba = 0

        self._start_lba = start_lba
        start_bytes = start_lba * 512
        end_bytes = end_lba * 512 if end_lba > 0 else 0

        self._worker = _SurfaceScanWorker(
            self._drive_number, self._capacity_bytes, block_size, mode,
            erase_slow, start_bytes, end_bytes,
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
        self._worker.bad_sector.connect(self._on_bad_sector)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)

        self._refresh_timer.start()
        self._thread.start()

    def _stop_scan(self):
        self.stop()
        self._status.setText(tr("Cancelled", "Отменено"))
        self._status.setStyleSheet("color: #f9e2af;")

    def _on_block_scanned(self, block_index: int, category_value: int, latency_ms: float):
        """Обновление данных карты (вызывается из worker thread через signal)."""
        self._block_map.set_block(block_index, category_value)
        self._counts[category_value] = self._counts.get(category_value, 0) + 1

    def _on_bad_sector(self, lba: int):
        """Битый сектор найден — добавляем в список (реалтайм)."""
        self._bad_lbas.append(lba)

    def _on_refresh_tick(self):
        """Обновление GUI по таймеру (~30 fps)."""
        self._block_map.flush()
        self._stats.update_counts(self._counts)
        if self._bad_lbas:
            self._stats.update_bad_sectors(self._bad_lbas)

        # Скорость и ETA
        if self._worker and self._scan_start_time > 0:
            elapsed = time.perf_counter() - self._scan_start_time
            total = self._worker.total_blocks
            scanned = sum(self._counts.values())
            speed_mbps = (scanned * self._current_block_size) / (1024 * 1024) / elapsed if elapsed > 0 else 0
            remaining = total - scanned
            eta_sec = remaining * (elapsed / scanned) if scanned > 0 else 0

            self._stats.update_info(speed_mbps, elapsed, eta_sec, scanned, total,
                                    self._current_block_size, self._start_lba)

    def _on_progress(self, fraction: float, message: str):
        self._progress.setValue(int(fraction * 1000))
        self._status.setText(message)

    def _on_finished(self, result: SurfaceScanResult):
        self._refresh_timer.stop()
        self._block_map.flush()

        self._progress.setValue(1000)
        self._btn_start.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._block_combo.setEnabled(True)
        self._mode_combo.setEnabled(True)
        self._slow_check.setEnabled(True)
        self._from_edit.setEnabled(True)
        self._to_edit.setEnabled(True)

        # Итоговая статистика
        self._stats.update_counts(result.counts)
        self._stats.update_info(
            result.avg_speed_mbps, result.elapsed_sec, 0,
            result.scanned_blocks, result.total_blocks,
            self._current_block_size, self._start_lba,
        )
        self._stats.update_repair_stats(result.repaired_blocks, result.write_errors)
        self._stats.update_bad_sectors(result.bad_sector_lbas)

        # Финальный статус
        parts = []
        if result.error_count > 0:
            parts.append(f"{result.error_count} ошибок")
        if result.repaired_blocks > 0:
            parts.append(f"{result.repaired_blocks} исправлено")
        if result.write_errors > 0:
            parts.append(f"{result.write_errors} ошибок записи")

        if result.error_count > 0 or result.write_errors > 0:
            self._status.setText(f"Done — {', '.join(parts)}")
            self._status.setStyleSheet("color: #f38ba8;")
        elif result.repaired_blocks > 0:
            self._status.setText(f"Готово — {result.repaired_blocks} блоков исправлено, без ошибок!")
            self._status.setStyleSheet("color: #a6e3a1;")
        else:
            self._status.setText(tr("Done — no errors!", "Готово — без ошибок!"))
            self._status.setStyleSheet("color: #a6e3a1;")

    def _on_error(self, error_msg: str):
        self._refresh_timer.stop()
        self._btn_start.setEnabled(self._drive_number is not None)
        self._btn_stop.setEnabled(False)
        self._block_combo.setEnabled(True)
        self._mode_combo.setEnabled(True)
        self._slow_check.setEnabled(True)
        self._from_edit.setEnabled(True)
        self._to_edit.setEnabled(True)
        self._status.setText(f"Ошибка: {error_msg}")
        self._status.setStyleSheet("color: #f38ba8;")
