"""Простая система локализации: English / Русский."""

import os
import sys
import logging

logger = logging.getLogger(__name__)

_lang = "ru"  # по умолчанию русский


def _get_config_path() -> str:
    """Путь к lang.cfg — рядом с exe или рядом с run.py."""
    if getattr(sys, 'frozen', False):
        # PyInstaller exe — рядом с exe
        return os.path.join(os.path.dirname(sys.executable), "lang.cfg")
    else:
        # Разработка — в корне проекта
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lang.cfg")


def _load_language():
    """Загрузить язык из конфиг-файла."""
    global _lang
    try:
        cfg = _get_config_path()
        if os.path.exists(cfg):
            with open(cfg, "r") as f:
                val = f.read().strip()
                if val in ("en", "ru"):
                    _lang = val
    except Exception:
        pass


def save_language(lang: str):
    """Сохранить выбор языка."""
    global _lang
    _lang = lang
    try:
        cfg = _get_config_path()
        with open(cfg, "w") as f:
            f.write(lang)
    except Exception as e:
        logger.warning(f"Cannot save language preference: {e}")


def get_language() -> str:
    return _lang


def tr(en: str, ru: str) -> str:
    """Вернуть строку на текущем языке."""
    return ru if _lang == "ru" else en


# Загружаем при импорте
_load_language()
