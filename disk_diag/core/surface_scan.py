"""Движок сканирования поверхности диска (Surface Scan).

Последовательное чтение всей поверхности диска с измерением
времени отклика каждого блока — аналог Victoria HDD.

Использует FILE_FLAG_NO_BUFFERING + VirtualAlloc (page-aligned буферы).
Последовательное чтение без seek — файловый указатель двигается сам.
Seek используется только для перескока после ошибки чтения.
"""

import time
import logging
from typing import Callable, Optional

from .winapi import DeviceHandle, AlignedBuffer, DiskAccessError
from .constants import FILE_FLAG_NO_BUFFERING
from .models import BlockCategory, SurfaceScanResult

logger = logging.getLogger(__name__)

# block_callback(block_index, category, latency_ms)
BlockCallback = Callable[[int, BlockCategory, float], None]
# progress_callback(fraction 0..1, message)
ProgressCallback = Callable[[float, str], None]

# Максимальное количество ошибок подряд до прерывания теста
MAX_CONSECUTIVE_ERRORS = 100

# Доступные размеры блоков (label, bytes)
BLOCK_SIZES = [
    ("64 KB",  64 * 1024),
    ("256 KB", 256 * 1024),
    ("1 MB",   1024 * 1024),
    ("4 MB",   4 * 1024 * 1024),
]

DEFAULT_BLOCK_SIZE = 1024 * 1024  # 1 MB


class SurfaceScanEngine:
    """Движок сканирования поверхности диска.

    Читает весь диск последовательно (без лишних seek),
    измеряет время отклика каждого блока и классифицирует его.
    """

    def __init__(self, drive_number: int, capacity_bytes: int,
                 block_size: int = DEFAULT_BLOCK_SIZE):
        self.drive_number = drive_number
        self.capacity_bytes = capacity_bytes
        self.block_size = block_size
        self._cancelled = False

    def cancel(self):
        """Отменить сканирование."""
        self._cancelled = True

    @property
    def total_blocks(self) -> int:
        """Общее количество блоков для сканирования."""
        n = self.capacity_bytes // self.block_size
        return max(n, 1)

    def scan(
        self,
        block_callback: Optional[BlockCallback] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> SurfaceScanResult:
        """Запустить сканирование поверхности.

        Args:
            block_callback: Вызывается после каждого блока (block_index, category, latency_ms).
            progress_callback: Вызывается периодически (fraction, message).

        Returns:
            SurfaceScanResult с итоговой статистикой.
        """
        self._cancelled = False
        total = self.total_blocks
        bs = self.block_size

        result = SurfaceScanResult(total_blocks=total)
        counts = {cat.value: 0 for cat in BlockCategory if cat != BlockCategory.PENDING}
        consecutive_errors = 0
        need_seek = False

        if progress_callback:
            progress_callback(0.0, "Starting surface scan...")

        scan_start = time.perf_counter()

        with DeviceHandle(self.drive_number, read_only=True, flags=FILE_FLAG_NO_BUFFERING) as h:
            with AlignedBuffer(bs) as buf:
                for i in range(total):
                    if self._cancelled:
                        break

                    try:
                        # Seek только после ошибки — обычно указатель уже на месте
                        if need_seek:
                            h.seek(i * bs)
                            need_seek = False

                        t0 = time.perf_counter()
                        h.read(buf.ptr, bs)
                        t1 = time.perf_counter()

                        latency_ms = (t1 - t0) * 1000.0
                        category = BlockCategory.from_latency_ms(latency_ms)
                        consecutive_errors = 0

                    except DiskAccessError:
                        latency_ms = 0.0
                        category = BlockCategory.ERROR
                        result.error_count += 1
                        result.error_offsets.append(i * bs)
                        consecutive_errors += 1
                        need_seek = True  # после ошибки позиция файла неопределена

                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            logger.warning(
                                f"Surface scan aborted: {MAX_CONSECUTIVE_ERRORS} "
                                f"consecutive I/O errors at block {i}"
                            )
                            break

                    counts[category.value] = counts.get(category.value, 0) + 1
                    result.scanned_blocks = i + 1

                    if block_callback:
                        block_callback(i, category, latency_ms)

                    # Прогресс каждые 100 блоков или на последнем
                    if progress_callback and (i % 100 == 0 or i == total - 1):
                        elapsed = time.perf_counter() - scan_start
                        scanned_bytes = (i + 1) * bs
                        speed_mbps = scanned_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0
                        pct = (i + 1) / total
                        progress_callback(pct, f"{speed_mbps:.1f} MB/s \u2014 {pct * 100:.1f}%")

        elapsed = time.perf_counter() - scan_start
        scanned_bytes = result.scanned_blocks * bs

        result.counts = counts
        result.elapsed_sec = elapsed
        result.avg_speed_mbps = (
            scanned_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0
        )

        logger.info(
            f"Surface scan: {result.scanned_blocks}/{total} blocks ({bs // 1024} KB), "
            f"{result.error_count} errors, "
            f"{result.avg_speed_mbps:.1f} MB/s, {elapsed:.1f}s"
        )

        return result
