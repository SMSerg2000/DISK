"""Движок бенчмарка: последовательное и случайное чтение.

Использует FILE_FLAG_NO_BUFFERING для обхода кэша Windows.
Все буферы выровнены по страницам (VirtualAlloc).
"""

import time
import random
import logging
from typing import Callable, Optional

from .winapi import DeviceHandle, AlignedBuffer, DiskAccessError
from .constants import FILE_FLAG_NO_BUFFERING
from .models import BenchmarkResult

logger = logging.getLogger(__name__)

# callback(phase: str, progress: float 0..1, message: str)
ProgressCallback = Callable[[str, float, str], None]


class BenchmarkEngine:
    """Движок бенчмарка диска.

    Тесты:
        1. Sequential Read — последовательное чтение блоками 1 MB
        2. Random 4K Read — случайное чтение блоками 4 KB (IOPS + латентность)
    """

    SEQUENTIAL_BLOCK = 1024 * 1024           # 1 MB
    SEQUENTIAL_TOTAL = 512 * 1024 * 1024     # 512 MB
    RANDOM_BLOCK = 4096                       # 4 KB
    RANDOM_COUNT = 1000                       # количество случайных чтений

    def __init__(self, drive_number: int, capacity_bytes: int):
        self.drive_number = drive_number
        self.capacity_bytes = capacity_bytes
        self._cancelled = False

    def cancel(self):
        """Отменить текущий тест."""
        self._cancelled = True

    def run(self, progress: Optional[ProgressCallback] = None) -> BenchmarkResult:
        """Запустить полный набор бенчмарков.

        Args:
            progress: Callback для отображения прогресса в GUI.

        Returns:
            BenchmarkResult с результатами всех тестов.
        """
        self._cancelled = False
        result = BenchmarkResult()

        # Фаза 1: Sequential Read
        if not self._cancelled:
            self._run_sequential(result, progress)

        # Фаза 2: Random 4K Read
        if not self._cancelled:
            self._run_random_4k(result, progress)

        return result

    def _run_sequential(self, result: BenchmarkResult, progress: Optional[ProgressCallback]):
        """Последовательное чтение 1 MB блоками."""
        total = min(self.SEQUENTIAL_TOTAL, self.capacity_bytes)
        blocks = total // self.SEQUENTIAL_BLOCK
        if blocks <= 0:
            return

        if progress:
            progress("sequential", 0.0, "Starting sequential read...")

        with DeviceHandle(self.drive_number, read_only=True, flags=FILE_FLAG_NO_BUFFERING) as h:
            with AlignedBuffer(self.SEQUENTIAL_BLOCK) as buf:
                bytes_read = 0
                start = time.perf_counter()

                for i in range(blocks):
                    if self._cancelled:
                        break

                    n = h.read(buf.ptr, self.SEQUENTIAL_BLOCK)
                    bytes_read += n

                    if progress and (i % 10 == 0 or i == blocks - 1):
                        elapsed = time.perf_counter() - start
                        speed = bytes_read / (1024 * 1024) / elapsed if elapsed > 0 else 0
                        progress("sequential", (i + 1) / blocks, f"{speed:.1f} MB/s")

                elapsed = time.perf_counter() - start

        result.sequential_bytes_read = bytes_read
        result.sequential_time_sec = elapsed
        result.sequential_speed_mbps = (
            bytes_read / (1024 * 1024) / elapsed if elapsed > 0 else 0
        )

        logger.info(
            f"Sequential read: {result.sequential_speed_mbps:.1f} MB/s "
            f"({bytes_read / (1024*1024):.0f} MB in {elapsed:.2f}s)"
        )

    def _run_random_4k(self, result: BenchmarkResult, progress: Optional[ProgressCallback]):
        """Случайное чтение 4 KB блоками."""
        # Максимальное выровненное смещение
        max_offset = (self.capacity_bytes - self.RANDOM_BLOCK) // self.RANDOM_BLOCK * self.RANDOM_BLOCK
        if max_offset <= 0:
            return

        if progress:
            progress("random", 0.0, "Starting random 4K read...")

        # Генерируем случайные смещения, выровненные по 4 KB
        offsets = [
            random.randrange(0, max_offset, self.RANDOM_BLOCK)
            for _ in range(self.RANDOM_COUNT)
        ]

        latencies: list[float] = []
        latency_points: list[tuple[float, float]] = []

        with DeviceHandle(self.drive_number, read_only=True, flags=FILE_FLAG_NO_BUFFERING) as h:
            with AlignedBuffer(self.RANDOM_BLOCK) as buf:
                wall_start = time.perf_counter()

                for i, offset in enumerate(offsets):
                    if self._cancelled:
                        break

                    t0 = time.perf_counter()
                    h.read_at(offset, buf.ptr, self.RANDOM_BLOCK)
                    t1 = time.perf_counter()

                    latency_us = (t1 - t0) * 1_000_000
                    latencies.append(latency_us)

                    offset_gb = offset / (1024 ** 3)
                    latency_points.append((offset_gb, latency_us))

                    if progress and (i % 50 == 0 or i == self.RANDOM_COUNT - 1):
                        avg = sum(latencies) / len(latencies)
                        progress("random", (i + 1) / self.RANDOM_COUNT,
                                 f"{len(latencies)} reads, avg {avg:.0f} μs")

                wall_elapsed = time.perf_counter() - wall_start

        if latencies:
            result.random_iops = len(latencies) / wall_elapsed if wall_elapsed > 0 else 0
            result.random_avg_latency_us = sum(latencies) / len(latencies)
            result.random_min_latency_us = min(latencies)
            result.random_max_latency_us = max(latencies)
            result.random_reads_count = len(latencies)
            result.latency_points = latency_points

        logger.info(
            f"Random 4K: {result.random_iops:,.0f} IOPS, "
            f"avg latency {result.random_avg_latency_us:.1f} μs "
            f"({len(latencies)} reads in {wall_elapsed:.2f}s)"
        )
