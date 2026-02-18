"""Проверка и запрос прав администратора."""

import ctypes
import sys


def is_admin() -> bool:
    """Проверить, запущен ли процесс с правами администратора."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def request_admin_restart():
    """Перезапустить приложение с запросом UAC elevation."""
    ctypes.windll.shell32.ShellExecuteW(
        None,           # hwnd
        "runas",        # operation
        sys.executable, # file
        " ".join(sys.argv),  # parameters
        None,           # directory
        1,              # SW_SHOWNORMAL
    )
    sys.exit(0)
