"""Низкоуровневый доступ к Windows API для работы с дисками."""

import ctypes
import ctypes.wintypes as wintypes
import logging

from .constants import (
    GENERIC_READ, GENERIC_WRITE, OPEN_EXISTING,
    FILE_SHARE_READ, FILE_SHARE_WRITE,
    FILE_FLAG_NO_BUFFERING, FILE_BEGIN,
    MEM_COMMIT, MEM_RESERVE, MEM_RELEASE, PAGE_READWRITE,
)

logger = logging.getLogger(__name__)

kernel32 = ctypes.windll.kernel32

# INVALID_HANDLE_VALUE = (HANDLE)-1
# На 64-bit: 0xFFFFFFFFFFFFFFFF = 18446744073709551615
_INVALID_HANDLE = ctypes.c_void_p(-1).value


# --- Exceptions ---

class DiskAccessError(Exception):
    """Базовая ошибка доступа к диску."""


class AdminPrivilegeRequired(DiskAccessError):
    """Требуются права администратора."""


class DriveNotFound(DiskAccessError):
    """Физический диск не найден."""


class SmartNotSupported(DiskAccessError):
    """Диск не поддерживает SMART."""


class IoctlFailed(DiskAccessError):
    """DeviceIoControl вернул ошибку."""
    def __init__(self, ioctl_name: str, error_code: int, error_msg: str = ""):
        self.ioctl_name = ioctl_name
        self.error_code = error_code
        msg = f"{ioctl_name} failed: error {error_code}"
        if error_msg:
            msg += f" ({error_msg})"
        super().__init__(msg)


# --- Win32 API setup ---

_CreateFileW = kernel32.CreateFileW
_CreateFileW.restype = wintypes.HANDLE
_CreateFileW.argtypes = [
    wintypes.LPCWSTR,  # lpFileName
    wintypes.DWORD,    # dwDesiredAccess
    wintypes.DWORD,    # dwShareMode
    ctypes.c_void_p,   # lpSecurityAttributes
    wintypes.DWORD,    # dwCreationDisposition
    wintypes.DWORD,    # dwFlagsAndAttributes
    wintypes.HANDLE,   # hTemplateFile
]

_CloseHandle = kernel32.CloseHandle
_CloseHandle.restype = wintypes.BOOL
_CloseHandle.argtypes = [wintypes.HANDLE]

_DeviceIoControl = kernel32.DeviceIoControl
_DeviceIoControl.restype = wintypes.BOOL
_DeviceIoControl.argtypes = [
    wintypes.HANDLE,   # hDevice
    wintypes.DWORD,    # dwIoControlCode
    ctypes.c_void_p,   # lpInBuffer
    wintypes.DWORD,    # nInBufferSize
    ctypes.c_void_p,   # lpOutBuffer
    wintypes.DWORD,    # nOutBufferSize
    ctypes.POINTER(wintypes.DWORD),  # lpBytesReturned
    ctypes.c_void_p,   # lpOverlapped
]

_GetLastError = kernel32.GetLastError
_FormatMessageW = kernel32.FormatMessageW

# --- ReadFile ---
_ReadFile = kernel32.ReadFile
_ReadFile.restype = wintypes.BOOL
_ReadFile.argtypes = [
    wintypes.HANDLE,                    # hFile
    ctypes.c_void_p,                    # lpBuffer
    wintypes.DWORD,                     # nNumberOfBytesToRead
    ctypes.POINTER(wintypes.DWORD),     # lpNumberOfBytesRead
    ctypes.c_void_p,                    # lpOverlapped
]

# --- SetFilePointerEx ---
_SetFilePointerEx = kernel32.SetFilePointerEx
_SetFilePointerEx.restype = wintypes.BOOL
_SetFilePointerEx.argtypes = [
    wintypes.HANDLE,                            # hFile
    wintypes.LARGE_INTEGER,                     # liDistanceToMove
    ctypes.POINTER(wintypes.LARGE_INTEGER),     # lpNewFilePointer
    wintypes.DWORD,                             # dwMoveMethod
]

# --- VirtualAlloc / VirtualFree (для выровненных буферов) ---
_VirtualAlloc = kernel32.VirtualAlloc
_VirtualAlloc.restype = ctypes.c_void_p
_VirtualAlloc.argtypes = [
    ctypes.c_void_p,    # lpAddress
    ctypes.c_size_t,    # dwSize
    wintypes.DWORD,     # flAllocationType
    wintypes.DWORD,     # flProtect
]

_VirtualFree = kernel32.VirtualFree
_VirtualFree.restype = wintypes.BOOL
_VirtualFree.argtypes = [
    ctypes.c_void_p,    # lpAddress
    ctypes.c_size_t,    # dwSize
    wintypes.DWORD,     # dwFreeType
]


def _get_error_message(error_code: int) -> str:
    """Получить текстовое описание Windows-ошибки."""
    buf = ctypes.create_unicode_buffer(256)
    kernel32.FormatMessageW(
        0x1000,  # FORMAT_MESSAGE_FROM_SYSTEM
        None,
        error_code,
        0,
        buf,
        256,
        None,
    )
    return buf.value.strip()


# --- AlignedBuffer ---

class AlignedBuffer:
    """Page-aligned буфер через VirtualAlloc для FILE_FLAG_NO_BUFFERING I/O.

    FILE_FLAG_NO_BUFFERING требует буфер, выровненный по размеру сектора.
    VirtualAlloc возвращает page-aligned (4096) память — этого достаточно.
    """

    def __init__(self, size: int):
        self.size = size
        self.ptr = _VirtualAlloc(None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not self.ptr:
            raise MemoryError(f"VirtualAlloc failed for {size} bytes")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.free()

    def free(self):
        if self.ptr:
            _VirtualFree(self.ptr, 0, MEM_RELEASE)
            self.ptr = None


# --- DeviceHandle ---

class DeviceHandle:
    """Context manager для безопасного открытия/закрытия PhysicalDrive."""

    def __init__(self, drive_number: int, read_only: bool = False, flags: int = 0):
        self.drive_number = drive_number
        self.read_only = read_only
        self.flags = flags
        self._handle = None

    def __enter__(self) -> "DeviceHandle":
        path = f"\\\\.\\PhysicalDrive{self.drive_number}"
        access = GENERIC_READ if self.read_only else (GENERIC_READ | GENERIC_WRITE)
        share = FILE_SHARE_READ | FILE_SHARE_WRITE

        # Сбрасываем ошибку перед вызовом
        kernel32.SetLastError(0)

        self._handle = _CreateFileW(path, access, share, None, OPEN_EXISTING, self.flags, None)

        # Проверяем handle: None (c_void_p для NULL) или INVALID_HANDLE_VALUE
        handle_failed = (
            self._handle is None
            or self._handle == _INVALID_HANDLE
            or self._handle == -1
            or self._handle == 0
        )

        if handle_failed:
            error_code = _GetLastError()
            error_msg = _get_error_message(error_code)
            logger.debug(
                f"CreateFileW({path}) failed: handle={self._handle}, "
                f"error={error_code} ({error_msg})"
            )
            if error_code == 5:  # ERROR_ACCESS_DENIED
                raise AdminPrivilegeRequired(
                    f"Нет доступа к PhysicalDrive{self.drive_number}. "
                    f"Запустите от администратора."
                )
            elif error_code == 2:  # ERROR_FILE_NOT_FOUND
                raise DriveNotFound(f"PhysicalDrive{self.drive_number} не найден.")
            else:
                raise DiskAccessError(
                    f"Не удалось открыть PhysicalDrive{self.drive_number}: "
                    f"error {error_code} ({error_msg})"
                )

        logger.info(
            f"Opened PhysicalDrive{self.drive_number}, "
            f"handle=0x{self._handle:X}, read_only={self.read_only}"
        )
        return self

    def __exit__(self, *args):
        if self._handle is not None:
            _CloseHandle(self._handle)
            logger.debug(f"Closed PhysicalDrive{self.drive_number}")
            self._handle = None

    @property
    def handle(self):
        return self._handle

    def ioctl(
        self,
        ioctl_code: int,
        in_buffer: ctypes.Structure | None,
        out_buffer_size: int,
    ) -> bytes:
        """Выполнить DeviceIoControl и вернуть выходной буфер.

        Args:
            ioctl_code: Код IOCTL
            in_buffer: Входная структура (или None)
            out_buffer_size: Размер выходного буфера в байтах

        Returns:
            Содержимое выходного буфера как bytes

        Raises:
            IoctlFailed: Если DeviceIoControl вернул ошибку
        """
        out_buffer = (ctypes.c_ubyte * out_buffer_size)()
        bytes_returned = wintypes.DWORD(0)

        in_ptr = ctypes.byref(in_buffer) if in_buffer is not None else None
        in_size = ctypes.sizeof(in_buffer) if in_buffer is not None else 0

        result = _DeviceIoControl(
            self._handle,
            ioctl_code,
            in_ptr,
            in_size,
            ctypes.byref(out_buffer),
            out_buffer_size,
            ctypes.byref(bytes_returned),
            None,
        )

        if not result:
            error_code = _GetLastError()
            error_msg = _get_error_message(error_code)
            raise IoctlFailed(
                f"IOCTL 0x{ioctl_code:08X}",
                error_code,
                error_msg,
            )

        return bytes(out_buffer[:bytes_returned.value])

    def seek(self, offset: int):
        """Установить файловый указатель на абсолютное смещение."""
        result = _SetFilePointerEx(self._handle, offset, None, FILE_BEGIN)
        if not result:
            error_code = _GetLastError()
            error_msg = _get_error_message(error_code)
            raise DiskAccessError(
                f"SetFilePointerEx({offset}) failed: error {error_code} ({error_msg})"
            )

    def read(self, buffer_ptr, size: int) -> int:
        """Прочитать данные с текущей позиции в буфер.

        Args:
            buffer_ptr: Указатель на буфер (c_void_p или AlignedBuffer.ptr)
            size: Количество байт для чтения

        Returns:
            Количество прочитанных байт
        """
        bytes_read = wintypes.DWORD(0)
        result = _ReadFile(self._handle, buffer_ptr, size, ctypes.byref(bytes_read), None)
        if not result:
            error_code = _GetLastError()
            error_msg = _get_error_message(error_code)
            raise DiskAccessError(
                f"ReadFile({size}) failed: error {error_code} ({error_msg})"
            )
        return bytes_read.value

    def read_at(self, offset: int, buffer_ptr, size: int) -> int:
        """Seek + Read: прочитать данные по абсолютному смещению.

        Args:
            offset: Абсолютное смещение на диске (должно быть выровнено по сектору
                     при использовании FILE_FLAG_NO_BUFFERING)
            buffer_ptr: Указатель на выровненный буфер
            size: Количество байт для чтения

        Returns:
            Количество прочитанных байт
        """
        self.seek(offset)
        return self.read(buffer_ptr, size)

    def ioctl_raw(
        self,
        ioctl_code: int,
        in_buffer: bytes,
        out_buffer_size: int,
    ) -> bytes:
        """DeviceIoControl с сырым буфером вместо структуры."""
        in_buf = (ctypes.c_ubyte * len(in_buffer))(*in_buffer)
        out_buffer = (ctypes.c_ubyte * out_buffer_size)()
        bytes_returned = wintypes.DWORD(0)

        result = _DeviceIoControl(
            self._handle,
            ioctl_code,
            ctypes.byref(in_buf),
            len(in_buffer),
            ctypes.byref(out_buffer),
            out_buffer_size,
            ctypes.byref(bytes_returned),
            None,
        )

        if not result:
            error_code = _GetLastError()
            error_msg = _get_error_message(error_code)
            raise IoctlFailed(
                f"IOCTL 0x{ioctl_code:08X}",
                error_code,
                error_msg,
            )

        return bytes(out_buffer[:bytes_returned.value])
