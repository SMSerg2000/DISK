"""Инициализация и запуск приложения."""

import sys
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from .gui.main_window import MainWindow
from .gui.theme import DARK_THEME_QSS
from . import __app_name__


def setup_logging():
    """Настройка логирования в консоль и файл."""
    import os

    log_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else "."
    log_file = os.path.join(log_dir, "disk_diag.log")

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, mode="w", encoding="utf-8"),
        ],
    )


def run_application():
    """Создать QApplication, применить тему, показать главное окно."""
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info(f"Starting {__app_name__}...")

    app = QApplication(sys.argv)
    app.setApplicationName(__app_name__)
    app.setStyle("Fusion")  # Fusion — лучшая база для кастомизации

    # Применяем тёмную тему
    app.setStyleSheet(DARK_THEME_QSS)

    # Шрифт по умолчанию
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()

    logger.info("Application started")
    sys.exit(app.exec())
