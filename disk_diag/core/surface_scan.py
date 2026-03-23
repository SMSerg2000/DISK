"""Движок сканирования поверхности диска (Surface Scan).

Последовательное чтение всей поверхности диска с измерением
времени отклика каждого блока — аналог Victoria HDD.

Режимы работы:
  - Ignore:  только чтение (по умолчанию)
  - Erase:   при ошибке — посекторное лечение (нули только в битые секторы)
  - Refresh: чтение → перезапись тех же данных (освежает деградирующие секторы)

При ошибке чтения блока — drill-down на уровень секторов:
читаем блок посекторно, пишем нули ТОЛЬКО в нечитаемые секторы.
Хорошие секторы не затрагиваются (данные сохраняются).

Использует FILE_FLAG_NO_BUFFERING + VirtualAlloc (page-aligned буферы).
"""

import ctypes
import time
import logging
from typing import Callable, Optional

from .winapi import DeviceHandle, AlignedBuffer, DiskAccessError, lock_and_dismount_volumes, unlock_volumes
from .constants import FILE_FLAG_NO_BUFFERING
from .models import BlockCategory, ScanMode, SurfaceScanResult

logger = logging.getLogger(__name__)

# block_callback(block_index, category, latency_ms)
BlockCallback = Callable[[int, BlockCategory, float], None]
# progress_callback(fraction 0..1, message)
ProgressCallback = Callable[[float, str], None]
# bad_sector_callback(lba)
BadSectorCallback = Callable[[int], None]

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

# Размер сектора для посекторного drill-down (4096 — безопасно для всех дисков)
_SECTOR_SIZE = 4096

# Категории, при которых Erase+Slow пишет нули во весь блок
_ERASE_SLOW_CATEGORIES = {BlockCategory.CRITICAL, BlockCategory.VERY_SLOW}


class SurfaceScanEngine:
    """Движок сканирования поверхности диска.

    Читает весь диск последовательно (без лишних seek),
    измеряет время отклика каждого блока и классифицирует его.
    В режимах Erase/Refresh — выполняет запись для лечения.
    """

    def __init__(self, drive_number: int, capacity_bytes: int,
                 block_size: int = DEFAULT_BLOCK_SIZE,
                 mode: ScanMode = ScanMode.IGNORE,
                 erase_slow: bool = False,
                 start_offset: int = 0,
                 end_offset: int = 0):
        self.drive_number = drive_number
        self.capacity_bytes = capacity_bytes
        self.block_size = block_size
        self.mode = mode
        self.erase_slow = erase_slow
        # Диапазон сканирования (0 = начало/конец диска)
        self.start_offset = (start_offset // block_size) * block_size  # выравниваем
        self.end_offset = end_offset if end_offset > 0 else capacity_bytes
        self.end_offset = (self.end_offset // block_size) * block_size
        self._cancelled = False

    def cancel(self):
        """Отменить сканирование."""
        self._cancelled = True

    @property
    def total_blocks(self) -> int:
        """Общее количество блоков для сканирования."""
        scan_bytes = self.end_offset - self.start_offset
        n = scan_bytes // self.block_size
        return max(n, 1)

    def scan(
        self,
        block_callback: Optional[BlockCallback] = None,
        progress_callback: Optional[ProgressCallback] = None,
        bad_sector_callback: Optional[BadSectorCallback] = None,
    ) -> SurfaceScanResult:
        """Запустить сканирование поверхности."""
        self._cancelled = False
        total = self.total_blocks
        bs = self.block_size
        writing = self.mode != ScanMode.IGNORE

        result = SurfaceScanResult(total_blocks=total)
        counts = {cat.value: 0 for cat in BlockCategory if cat != BlockCategory.PENDING}
        consecutive_errors = 0
        need_seek = False

        if progress_callback:
            progress_callback(0.0, "Starting surface scan...")

        # WRITE: блокируем и размонтируем тома, чтобы Windows разрешила запись
        volume_handles = []
        if self.mode == ScanMode.WRITE:
            if progress_callback:
                progress_callback(0.0, "Locking volumes...")
            volume_handles = lock_and_dismount_volumes(self.drive_number)
            logger.info(f"Locked {len(volume_handles)} volume(s) on drive {self.drive_number}")

        scan_start = time.perf_counter()

        with DeviceHandle(self.drive_number, read_only=not writing,
                          flags=FILE_FLAG_NO_BUFFERING) as h:
            with AlignedBuffer(bs) as buf:
                write_buf = None
                # Sector buffer нужен всегда (drill-down даже в Ignore)
                sector_buf = AlignedBuffer(_SECTOR_SIZE)
                if writing:
                    write_buf = AlignedBuffer(bs)

                # Если начинаем не с нуля — seek на start_offset
                if self.start_offset > 0:
                    h.seek(self.start_offset)

                try:
                    for i in range(total):
                        if self._cancelled:
                            break

                        # Абсолютное смещение блока на диске
                        block_abs_offset = self.start_offset + i * bs

                        read_ok = False

                        if self.mode == ScanMode.WRITE:
                            # ── WRITE: запись нулей без чтения ──
                            try:
                                if need_seek:
                                    h.seek(block_abs_offset)
                                    need_seek = False

                                ctypes.memset(write_buf.ptr, 0, bs)
                                t0 = time.perf_counter()
                                h.write(write_buf.ptr, bs)
                                t1 = time.perf_counter()

                                latency_ms = (t1 - t0) * 1000.0
                                category = BlockCategory.from_latency_ms(latency_ms)
                                consecutive_errors = 0
                                result.repaired_blocks += 1

                            except DiskAccessError:
                                latency_ms = 0.0
                                category = BlockCategory.ERROR
                                result.error_count += 1
                                result.error_offsets.append(block_abs_offset)
                                consecutive_errors += 1
                                need_seek = True

                                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                    logger.warning(
                                        f"Surface scan aborted: {MAX_CONSECUTIVE_ERRORS} "
                                        f"consecutive write errors at block {i}"
                                    )
                                    break

                                # Drill-down: пишем нули посекторно
                                bad_lbas, repaired, w_err = self._drill_down(
                                    h, block_abs_offset, bs, sector_buf, True,
                                    bad_sector_callback,
                                )
                                result.bad_sector_lbas.extend(bad_lbas)
                                result.repaired_blocks += repaired
                                result.write_errors += w_err

                        else:
                            # ── READ-based modes: Ignore / Erase / Refresh ──
                            try:
                                if need_seek:
                                    h.seek(block_abs_offset)
                                    need_seek = False

                                t0 = time.perf_counter()
                                h.read(buf.ptr, bs)
                                t1 = time.perf_counter()

                                latency_ms = (t1 - t0) * 1000.0
                                category = BlockCategory.from_latency_ms(latency_ms)
                                consecutive_errors = 0
                                read_ok = True

                            except DiskAccessError:
                                latency_ms = 0.0
                                category = BlockCategory.ERROR
                                result.error_count += 1
                                result.error_offsets.append(block_abs_offset)
                                consecutive_errors += 1
                                need_seek = True

                                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                                    logger.warning(
                                        f"Surface scan aborted: {MAX_CONSECUTIVE_ERRORS} "
                                        f"consecutive I/O errors at block {i}"
                                    )
                                    break

                            # --- Drill-down при ошибке + логика записи ---
                            if category == BlockCategory.ERROR:
                                do_write = writing and self._should_write(category, read_ok)
                                bad_lbas, repaired, w_err = self._drill_down(
                                    h, block_abs_offset, bs, sector_buf, do_write,
                                    bad_sector_callback,
                                )
                                result.bad_sector_lbas.extend(bad_lbas)
                                result.repaired_blocks += repaired
                                result.write_errors += w_err
                                need_seek = True

                            elif writing and self._should_write(category, read_ok):
                                if self.mode == ScanMode.REFRESH and read_ok:
                                    try:
                                        ctypes.memmove(write_buf.ptr, buf.ptr, bs)
                                        h.write_at(block_abs_offset, write_buf.ptr, bs)
                                        result.repaired_blocks += 1
                                        need_seek = True
                                    except DiskAccessError as e:
                                        result.write_errors += 1
                                        need_seek = True
                                        logger.debug(f"Refresh write failed at block {i}: {e}")
                                else:
                                    try:
                                        ctypes.memset(write_buf.ptr, 0, bs)
                                        h.write_at(block_abs_offset, write_buf.ptr, bs)
                                        result.repaired_blocks += 1
                                        need_seek = True
                                    except DiskAccessError as e:
                                        result.write_errors += 1
                                        need_seek = True
                                        logger.debug(f"Erase write failed at block {i}: {e}")

                        counts[category.value] = counts.get(category.value, 0) + 1
                        result.scanned_blocks = i + 1

                        if block_callback:
                            block_callback(i, category, latency_ms)

                        if progress_callback and (i % 100 == 0 or i == total - 1):
                            elapsed = time.perf_counter() - scan_start
                            scanned_bytes = (i + 1) * bs
                            speed_mbps = scanned_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0
                            pct = (i + 1) / total
                            progress_callback(pct, f"{speed_mbps:.1f} MB/s \u2014 {pct * 100:.1f}%")
                finally:
                    sector_buf.free()
                    if write_buf:
                        write_buf.free()

        # Разблокируем тома после записи
        if volume_handles:
            unlock_volumes(volume_handles)
            logger.info(f"Unlocked {len(volume_handles)} volume(s)")

        elapsed = time.perf_counter() - scan_start
        scanned_bytes = result.scanned_blocks * bs

        result.counts = counts
        result.elapsed_sec = elapsed
        result.avg_speed_mbps = (
            scanned_bytes / (1024 * 1024) / elapsed if elapsed > 0 else 0
        )

        logger.info(
            f"Surface scan [{self.mode.value}]: "
            f"{result.scanned_blocks}/{total} blocks ({bs // 1024} KB), "
            f"{result.error_count} errors, {result.repaired_blocks} repaired, "
            f"{result.write_errors} write errors, "
            f"{result.avg_speed_mbps:.1f} MB/s, {elapsed:.1f}s"
        )

        return result

    def _should_write(self, category: BlockCategory, read_ok: bool) -> bool:
        """Определить, нужно ли обработать блок записью."""
        if self.mode == ScanMode.ERASE:
            if category == BlockCategory.ERROR:
                return True
            if self.erase_slow and category in _ERASE_SLOW_CATEGORIES:
                return True
            return False
        elif self.mode == ScanMode.REFRESH:
            return True
        return False

    def _drill_down(
        self,
        h: DeviceHandle,
        block_offset: int,
        block_size: int,
        sector_buf: AlignedBuffer,
        do_write: bool,
        bad_sector_callback: Optional[BadSectorCallback] = None,
    ) -> tuple[list[int], int, int]:
        """Посекторный drill-down блока с ошибкой чтения.

        Перечитывает блок по секторам (4096 байт), находит конкретные
        битые секторы и возвращает их LBA.

        do_write=True (Erase/Refresh): пишет нули в битые секторы.
        do_write=False (Ignore): только идентификация.

        Returns:
            (bad_lbas, repaired_count, write_error_count)
        """
        bad_lbas = []
        repaired = 0
        write_errors = 0
        sectors_in_block = block_size // _SECTOR_SIZE

        logger.debug(
            f"Drill-down: block at {block_offset}, "
            f"{sectors_in_block} sectors of {_SECTOR_SIZE}B"
        )

        for s in range(sectors_in_block):
            if self._cancelled:
                break

            sector_offset = block_offset + s * _SECTOR_SIZE
            sector_lba = sector_offset // 512  # LBA в 512-байтных секторах

            try:
                h.read_at(sector_offset, sector_buf.ptr, _SECTOR_SIZE)
                # Сектор прочитался
                if do_write and self.mode == ScanMode.REFRESH:
                    try:
                        h.write_at(sector_offset, sector_buf.ptr, _SECTOR_SIZE)
                        repaired += 1
                    except DiskAccessError:
                        write_errors += 1

            except DiskAccessError:
                # Сектор не читается
                bad_lbas.append(sector_lba)
                logger.info(f"Bad sector LBA {sector_lba} (offset {sector_offset})")
                if bad_sector_callback:
                    bad_sector_callback(sector_lba)

                if do_write:
                    try:
                        ctypes.memset(sector_buf.ptr, 0, _SECTOR_SIZE)
                        h.write_at(sector_offset, sector_buf.ptr, _SECTOR_SIZE)
                        repaired += 1
                    except DiskAccessError as e:
                        write_errors += 1
                        logger.debug(f"Sector write failed at LBA {sector_lba}: {e}")

        return bad_lbas, repaired, write_errors
