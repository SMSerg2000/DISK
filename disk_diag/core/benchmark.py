"""Движок бенчмарка: чтение, запись, SLC-кэш тест.

Использует FILE_FLAG_NO_BUFFERING для обхода кэша Windows.
Все буферы выровнены по страницам (VirtualAlloc).
"""

import ctypes
import os
import time
import random
import logging
from typing import Callable, Optional

from .winapi import (DeviceHandle, AlignedBuffer, DiskAccessError,
                     lock_and_dismount_volumes, unlock_volumes)
from .constants import FILE_FLAG_NO_BUFFERING, FILE_FLAG_WRITE_THROUGH
from .models import BenchmarkResult, InterfaceType

logger = logging.getLogger(__name__)

# callback(phase: str, progress: float 0..1, message: str)
ProgressCallback = Callable[[str, float, str], None]


def _read_temperature(drive_number: int, interface_type: str = "") -> int | None:
    """Быстро прочитать температуру диска (отдельный handle)."""
    try:
        if interface_type == "NVMe":
            from .smart_nvme import read_nvme_health_auto
            health = read_nvme_health_auto(drive_number)
            return health.temperature_celsius if health else None
        else:
            from .smart_ata import read_smart_attributes, get_temperature_from_smart
            with DeviceHandle(drive_number, read_only=True) as h:
                attrs = read_smart_attributes(h, drive_number)
                return get_temperature_from_smart(attrs)
    except Exception:
        return None


class BenchmarkEngine:
    """Движок бенчмарка диска.

    Тесты (чтение):
        1. Sequential Read — последовательное чтение блоками 1 MB
        2. Random 4K Read — случайное чтение блоками 4 KB
        3. Full Drive Read Sweep — скорость vs позиция (график)
    Тесты (запись, деструктивные):
        4. Sequential Write — последовательная запись 512 MB
        5. Random 4K Write — случайная запись 4 KB
        6. SLC Cache Test — непрерывная запись до cliff
    """

    SEQUENTIAL_BLOCK = 1024 * 1024           # 1 MB
    SEQUENTIAL_TOTAL = 512 * 1024 * 1024     # 512 MB
    RANDOM_BLOCK = 4096                       # 4 KB
    RANDOM_COUNT = 1000                       # количество случайных чтений/записей
    SLC_MAX_GB = 50                           # макс. объём записи SLC-теста (ГБ)
    SLC_SAMPLE_MB = 100                       # замер скорости каждые N МБ
    SWEEP_SAMPLE_MB = 50                      # замер при sweep каждые N МБ
    SWEEP_MAX_GB = 0                          # 0 = весь диск
    TEMP_INTERVAL_SEC = 5                     # опрос температуры

    def __init__(self, drive_number: int, capacity_bytes: int,
                 include_write: bool = False,
                 interface_type: str = ""):
        self.drive_number = drive_number
        self.capacity_bytes = capacity_bytes
        self.include_write = include_write
        self.interface_type = interface_type
        self._cancelled = False
        self._bench_start: float = 0.0
        self._last_temp_time: float = 0.0

    def cancel(self):
        """Отменить текущий тест."""
        self._cancelled = True

    def run(self, progress: Optional[ProgressCallback] = None) -> BenchmarkResult:
        """Запустить бенчмарк.

        Args:
            progress: Callback для отображения прогресса в GUI.

        Returns:
            BenchmarkResult с результатами всех тестов.
        """
        self._cancelled = False
        self._bench_start = time.perf_counter()
        self._last_temp_time = 0.0
        result = BenchmarkResult()

        # Начальная температура
        self._poll_temp(result)

        # Фаза 1: Sequential Read
        if not self._cancelled:
            self._run_sequential(result, progress)

        # Фаза 2: Random 4K Read
        if not self._cancelled:
            self._run_random_4k(result, progress)

        # Фаза 3: Full Drive Read Sweep
        if not self._cancelled:
            self._run_sweep(result, progress)

        # Фаза 4-8: Write тесты (деструктивные)
        if self.include_write and not self._cancelled:
            vol_handles = lock_and_dismount_volumes(self.drive_number)
            try:
                write_phases = [
                    ("seq_write", self._run_sequential_write),
                    ("rnd_write", self._run_random_4k_write),
                    ("mixed", self._run_mixed_io),
                    ("verify", self._run_verify),
                    ("slc_cache", self._run_slc_cache),
                ]
                for phase_name, phase_fn in write_phases:
                    if self._cancelled:
                        break
                    try:
                        phase_fn(result, progress)
                    except DiskAccessError as e:
                        logger.error(f"Benchmark {phase_name} I/O error: {e}")
                        result.io_errors.append(f"{phase_name}: {e}")
                        if progress:
                            progress(phase_name, 1.0, f"I/O Error: {e}")
            finally:
                unlock_volumes(vol_handles)

        return result

    def _poll_temp(self, result: BenchmarkResult):
        """Записать температуру, если прошло TEMP_INTERVAL_SEC с последнего замера."""
        now = time.perf_counter()
        if now - self._last_temp_time < self.TEMP_INTERVAL_SEC:
            return
        self._last_temp_time = now
        temp = _read_temperature(self.drive_number, self.interface_type)
        if temp is not None and temp > 0:
            elapsed = now - self._bench_start
            result.temp_log.append((elapsed, temp))

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
            # Percentiles
            sorted_lat = sorted(latencies)
            n = len(sorted_lat)
            result.random_p95_latency_us = sorted_lat[int(n * 0.95)]
            result.random_p99_latency_us = sorted_lat[min(int(n * 0.99), n - 1)]
            result.random_p999_latency_us = sorted_lat[min(int(n * 0.999), n - 1)]
            result.random_p9999_latency_us = sorted_lat[min(int(n * 0.9999), n - 1)]

        logger.info(
            f"Random 4K: {result.random_iops:,.0f} IOPS, "
            f"avg latency {result.random_avg_latency_us:.1f} μs "
            f"({len(latencies)} reads in {wall_elapsed:.2f}s)"
        )

    # ------------------------------------------------------------------
    #  Full Drive Read Sweep (speed vs position)
    # ------------------------------------------------------------------

    def _run_sweep(self, result: BenchmarkResult,
                   progress: Optional[ProgressCallback]):
        """Чтение скорости в ~200 точках по всему диску (skip-sampling)."""
        sample_bytes = self.SWEEP_SAMPLE_MB * 1024 * 1024  # 50 MB на замер
        sample_blocks = sample_bytes // self.SEQUENTIAL_BLOCK
        num_samples = 200
        disk_size = self.capacity_bytes

        if sample_blocks <= 0 or disk_size < sample_bytes * 2:
            return

        # Шаг между замерами (равномерно по диску)
        step = disk_size // num_samples
        # Выравниваем по 1 MB
        step = (step // self.SEQUENTIAL_BLOCK) * self.SEQUENTIAL_BLOCK
        if step < sample_bytes:
            step = sample_bytes

        if progress:
            progress("sweep", 0.0, "Drive read sweep (sampling)...")

        points: list[tuple[float, float]] = []

        with DeviceHandle(self.drive_number, read_only=True,
                          flags=FILE_FLAG_NO_BUFFERING) as h:
            with AlignedBuffer(self.SEQUENTIAL_BLOCK) as buf:
                for i in range(num_samples):
                    if self._cancelled:
                        break

                    offset = i * step
                    if offset + sample_bytes > disk_size:
                        break

                    h.seek(offset)

                    t0 = time.perf_counter()
                    chunk_read = 0
                    for _ in range(sample_blocks):
                        if self._cancelled:
                            break
                        try:
                            h.read(buf.ptr, self.SEQUENTIAL_BLOCK)
                            chunk_read += self.SEQUENTIAL_BLOCK
                        except DiskAccessError:
                            break
                    t1 = time.perf_counter()
                    elapsed = t1 - t0

                    if elapsed > 0 and chunk_read > 0:
                        speed = chunk_read / (1024 * 1024) / elapsed
                        pos_gb = offset / (1024 ** 3)
                        points.append((pos_gb, speed))

                    self._poll_temp(result)

                    if progress:
                        pct = (i + 1) / num_samples
                        spd = points[-1][1] if points else 0
                        progress("sweep", pct,
                                 f"{pos_gb:.0f} GB, {spd:.0f} MB/s")

        result.sweep_points = points
        logger.info(f"Drive sweep: {len(points)} samples across "
                    f"{disk_size / (1024**3):.0f} GB")

    # ------------------------------------------------------------------
    #  Random 4K Write (QD1)
    # ------------------------------------------------------------------

    def _run_random_4k_write(self, result: BenchmarkResult,
                             progress: Optional[ProgressCallback]):
        """Случайная запись 4 KB блоками (QD1)."""
        max_offset = (self.capacity_bytes - self.RANDOM_BLOCK) // self.RANDOM_BLOCK * self.RANDOM_BLOCK
        if max_offset <= 0:
            return

        if progress:
            progress("rnd_write", 0.0, "Starting random 4K write...")

        offsets = [
            random.randrange(0, max_offset, self.RANDOM_BLOCK)
            for _ in range(self.RANDOM_COUNT)
        ]

        # Случайные данные
        rand_data = os.urandom(self.RANDOM_BLOCK)
        latencies: list[float] = []

        with DeviceHandle(self.drive_number, read_only=False,
                          flags=FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH) as h:
            with AlignedBuffer(self.RANDOM_BLOCK) as buf:
                ctypes.memmove(buf.ptr, rand_data, self.RANDOM_BLOCK)
                wall_start = time.perf_counter()

                for i, offset in enumerate(offsets):
                    if self._cancelled:
                        break

                    t0 = time.perf_counter()
                    h.write_at(offset, buf.ptr, self.RANDOM_BLOCK)
                    t1 = time.perf_counter()

                    latencies.append((t1 - t0) * 1_000_000)

                    if progress and (i % 50 == 0 or i == self.RANDOM_COUNT - 1):
                        avg = sum(latencies) / len(latencies)
                        progress("rnd_write", (i + 1) / self.RANDOM_COUNT,
                                 f"{len(latencies)} writes, avg {avg:.0f} μs")

                wall_elapsed = time.perf_counter() - wall_start

        if latencies:
            result.random_write_iops = len(latencies) / wall_elapsed if wall_elapsed > 0 else 0
            result.random_write_avg_latency_us = sum(latencies) / len(latencies)
            result.random_write_count = len(latencies)

        logger.info(
            f"Random 4K Write: {result.random_write_iops:,.0f} IOPS, "
            f"avg {result.random_write_avg_latency_us:.1f} μs "
            f"({len(latencies)} writes in {wall_elapsed:.2f}s)"
        )

    # ------------------------------------------------------------------
    #  Mixed I/O (70% Read / 30% Write, Random 4K)
    # ------------------------------------------------------------------

    def _run_mixed_io(self, result: BenchmarkResult,
                      progress: Optional[ProgressCallback]):
        """Случайные 4K: 70% чтение + 30% запись (QD1, 30 сек)."""
        max_offset = (self.capacity_bytes - self.RANDOM_BLOCK) // self.RANDOM_BLOCK * self.RANDOM_BLOCK
        if max_offset <= 0:
            return

        duration_sec = 30
        rand_data = os.urandom(self.RANDOM_BLOCK)

        if progress:
            progress("mixed", 0.0, "Mixed I/O 70/30...")

        reads = 0
        writes = 0

        with DeviceHandle(self.drive_number, read_only=False,
                          flags=FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH) as h:
            with AlignedBuffer(self.RANDOM_BLOCK) as buf:
                ctypes.memmove(buf.ptr, rand_data, self.RANDOM_BLOCK)
                start = time.perf_counter()

                while not self._cancelled:
                    elapsed = time.perf_counter() - start
                    if elapsed >= duration_sec:
                        break

                    offset = random.randrange(0, max_offset, self.RANDOM_BLOCK)

                    if random.random() < 0.7:
                        # Read (70%)
                        h.read_at(offset, buf.ptr, self.RANDOM_BLOCK)
                        reads += 1
                    else:
                        # Write (30%)
                        h.write_at(offset, buf.ptr, self.RANDOM_BLOCK)
                        writes += 1

                    total_ops = reads + writes
                    if progress and total_ops % 200 == 0:
                        progress("mixed", elapsed / duration_sec,
                                 f"R:{reads} W:{writes}")

                wall_elapsed = time.perf_counter() - start

        total = reads + writes
        if wall_elapsed > 0 and total > 0:
            result.mixed_read_iops = reads / wall_elapsed
            result.mixed_write_iops = writes / wall_elapsed
            result.mixed_total_iops = total / wall_elapsed
            result.mixed_count = total

        logger.info(
            f"Mixed I/O 70/30: {result.mixed_total_iops:,.0f} total IOPS "
            f"(R:{result.mixed_read_iops:,.0f} W:{result.mixed_write_iops:,.0f}, "
            f"{total} ops in {wall_elapsed:.1f}s)"
        )

    # ------------------------------------------------------------------
    #  Write-Read-Verify (целостность данных)
    # ------------------------------------------------------------------

    VERIFY_BLOCK = 1024 * 1024           # 1 MB
    VERIFY_TOTAL = 256 * 1024 * 1024     # 256 MB

    def _run_verify(self, result: BenchmarkResult,
                    progress: Optional[ProgressCallback]):
        """Запись случайных данных → чтение → сравнение (CRC)."""
        import hashlib

        total = min(self.VERIFY_TOTAL, self.capacity_bytes)
        blocks = total // self.VERIFY_BLOCK
        if blocks <= 0:
            return

        if progress:
            progress("verify", 0.0, "Write-Read-Verify...")

        # Фаза 1: Запись случайных блоков + сохранение хешей
        hashes: list[bytes] = []
        with DeviceHandle(self.drive_number, read_only=False,
                          flags=FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH) as h:
            with AlignedBuffer(self.VERIFY_BLOCK) as buf:
                start = time.perf_counter()
                for i in range(blocks):
                    if self._cancelled:
                        break
                    data = os.urandom(self.VERIFY_BLOCK)
                    ctypes.memmove(buf.ptr, data, self.VERIFY_BLOCK)
                    h.write(buf.ptr, self.VERIFY_BLOCK)
                    hashes.append(hashlib.md5(data).digest())

                    if progress and i % 10 == 0:
                        progress("verify", (i + 1) / blocks * 0.5,
                                 f"Writing {i+1}/{blocks}...")

        if self._cancelled:
            return

        # Фаза 2: Чтение и проверка
        ok = 0
        fail = 0
        with DeviceHandle(self.drive_number, read_only=True,
                          flags=FILE_FLAG_NO_BUFFERING) as h:
            with AlignedBuffer(self.VERIFY_BLOCK) as buf:
                for i in range(len(hashes)):
                    if self._cancelled:
                        break
                    h.read(buf.ptr, self.VERIFY_BLOCK)
                    # Читаем данные из буфера
                    read_data = (ctypes.c_ubyte * self.VERIFY_BLOCK).from_address(buf.ptr)
                    read_hash = hashlib.md5(bytes(read_data)).digest()

                    if read_hash == hashes[i]:
                        ok += 1
                    else:
                        fail += 1
                        logger.warning(f"Verify MISMATCH at block {i}!")

                    if progress and i % 10 == 0:
                        progress("verify", 0.5 + (i + 1) / len(hashes) * 0.5,
                                 f"Verifying {i+1}/{len(hashes)}... "
                                 f"{'OK' if fail == 0 else f'{fail} FAIL!'}")

                elapsed = time.perf_counter() - start

        result.verify_blocks_tested = ok + fail
        result.verify_blocks_ok = ok
        result.verify_blocks_failed = fail
        result.verify_speed_mbps = (
            (ok + fail) * self.VERIFY_BLOCK / (1024 * 1024) / elapsed
            if elapsed > 0 else 0
        )

        status = "PASS" if fail == 0 else f"FAIL ({fail} blocks!)"
        logger.info(f"Write-Read-Verify: {status}, {ok+fail} blocks, "
                    f"{result.verify_speed_mbps:.1f} MB/s")

    # ------------------------------------------------------------------
    #  Sequential Write (512 MB)
    # ------------------------------------------------------------------

    def _run_sequential_write(self, result: BenchmarkResult,
                              progress: Optional[ProgressCallback]):
        """Последовательная запись 1 MB блоками (случайные данные)."""
        total = min(self.SEQUENTIAL_TOTAL, self.capacity_bytes)
        blocks = total // self.SEQUENTIAL_BLOCK
        if blocks <= 0:
            return

        if progress:
            progress("seq_write", 0.0, "Starting sequential write...")

        with DeviceHandle(self.drive_number, read_only=False,
                          flags=FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH) as h:
            with AlignedBuffer(self.SEQUENTIAL_BLOCK) as buf:
                # Заполняем буфер случайными данными (не нулями — контроллер может сжимать)
                rand_data = os.urandom(self.SEQUENTIAL_BLOCK)
                ctypes.memmove(buf.ptr, rand_data, self.SEQUENTIAL_BLOCK)

                bytes_written = 0
                start = time.perf_counter()

                for i in range(blocks):
                    if self._cancelled:
                        break

                    h.write(buf.ptr, self.SEQUENTIAL_BLOCK)
                    bytes_written += self.SEQUENTIAL_BLOCK

                    if progress and (i % 10 == 0 or i == blocks - 1):
                        elapsed = time.perf_counter() - start
                        speed = bytes_written / (1024 * 1024) / elapsed if elapsed > 0 else 0
                        progress("seq_write", (i + 1) / blocks, f"{speed:.1f} MB/s")

                elapsed = time.perf_counter() - start

        result.seq_write_bytes = bytes_written
        result.seq_write_time_sec = elapsed
        result.seq_write_speed_mbps = (
            bytes_written / (1024 * 1024) / elapsed if elapsed > 0 else 0
        )

        logger.info(
            f"Sequential write: {result.seq_write_speed_mbps:.1f} MB/s "
            f"({bytes_written / (1024*1024):.0f} MB in {elapsed:.2f}s)"
        )

    # ------------------------------------------------------------------
    #  SLC Cache Test
    # ------------------------------------------------------------------

    def _run_slc_cache(self, result: BenchmarkResult,
                       progress: Optional[ProgressCallback]):
        """Тест SLC-кэша: непрерывная запись с замером скорости.

        Пишем до SLC_MAX_GB или пока скорость не упадёт и стабилизируется.
        Записываем точки (written_gb, speed_mbps) для графика.
        """
        max_bytes = min(self.SLC_MAX_GB * 1024 ** 3, self.capacity_bytes)
        sample_bytes = self.SLC_SAMPLE_MB * 1024 * 1024
        sample_blocks = sample_bytes // self.SEQUENTIAL_BLOCK

        if sample_blocks <= 0:
            return

        if progress:
            progress("slc_cache", 0.0, "SLC Cache test starting...")

        points: list[tuple[float, float]] = []  # (written_gb, speed_mbps)
        total_written = 0

        with DeviceHandle(self.drive_number, read_only=False,
                          flags=FILE_FLAG_NO_BUFFERING | FILE_FLAG_WRITE_THROUGH) as h:
            with AlignedBuffer(self.SEQUENTIAL_BLOCK) as buf:
                # Случайные данные
                rand_data = os.urandom(self.SEQUENTIAL_BLOCK)
                ctypes.memmove(buf.ptr, rand_data, self.SEQUENTIAL_BLOCK)

                while total_written < max_bytes and not self._cancelled:
                    # Пишем sample_bytes и замеряем скорость
                    sample_written = 0
                    t0 = time.perf_counter()

                    for _ in range(sample_blocks):
                        if self._cancelled:
                            break
                        try:
                            h.write(buf.ptr, self.SEQUENTIAL_BLOCK)
                            sample_written += self.SEQUENTIAL_BLOCK
                        except DiskAccessError:
                            break

                    t1 = time.perf_counter()
                    elapsed = t1 - t0

                    total_written += sample_written
                    written_gb = total_written / (1024 ** 3)

                    if elapsed > 0 and sample_written > 0:
                        speed = sample_written / (1024 * 1024) / elapsed
                        points.append((written_gb, speed))

                    if progress:
                        pct = total_written / max_bytes
                        spd = points[-1][1] if points else 0
                        progress("slc_cache", pct,
                                 f"{written_gb:.1f} GB written, {spd:.0f} MB/s")

                    # Детектируем cliff: если скорость упала > 40% от первых 3 точек
                    # и стабилизировалась — пишем ещё 3 GB и останавливаемся
                    if len(points) >= 5:
                        initial_speed = sum(p[1] for p in points[:3]) / 3
                        current_speed = sum(p[1] for p in points[-3:]) / 3
                        if current_speed < initial_speed * 0.6:
                            # Скорость упала — пишем ещё 3 GB для стабилизации
                            post_cliff_bytes = 3 * 1024 ** 3
                            target = total_written + post_cliff_bytes
                            while total_written < target and not self._cancelled:
                                t0 = time.perf_counter()
                                sw = 0
                                for _ in range(sample_blocks):
                                    if self._cancelled:
                                        break
                                    try:
                                        h.write(buf.ptr, self.SEQUENTIAL_BLOCK)
                                        sw += self.SEQUENTIAL_BLOCK
                                    except DiskAccessError:
                                        break
                                t1 = time.perf_counter()
                                total_written += sw
                                el = t1 - t0
                                if el > 0 and sw > 0:
                                    s = sw / (1024 * 1024) / el
                                    points.append((total_written / (1024 ** 3), s))
                                if progress:
                                    progress("slc_cache", min(1.0, total_written / max_bytes),
                                             f"{total_written / (1024**3):.1f} GB, {s:.0f} MB/s (post-cache)")
                            break

        result.slc_points = points

        if len(points) >= 3:
            # Анализ: ищем cliff
            initial_speed = sum(p[1] for p in points[:3]) / 3
            result.slc_speed_mbps = initial_speed

            # Ищем точку перегиба (первое падение > 40%)
            cliff_gb = 0.0
            post_speeds = []
            for i, (gb, spd) in enumerate(points):
                if spd < initial_speed * 0.6 and i >= 3:
                    if cliff_gb == 0:
                        cliff_gb = gb
                    post_speeds.append(spd)

            result.slc_cache_size_gb = cliff_gb
            if post_speeds:
                result.slc_post_cache_speed_mbps = sum(post_speeds) / len(post_speeds)

        logger.info(
            f"SLC Cache: {result.slc_cache_size_gb:.1f} GB, "
            f"SLC speed: {result.slc_speed_mbps:.0f} MB/s, "
            f"post-cache: {result.slc_post_cache_speed_mbps:.0f} MB/s, "
            f"total written: {total_written / (1024**3):.1f} GB"
        )
