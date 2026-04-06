"""Vendor-specific SMART decoder profiles.

Каждый профиль описывает:
- match: по каким признакам определить контроллер (модель, прошивка)
- decode: как декодировать raw-значения SMART атрибутов
- name: человеко-читаемое название профиля

Методы декодирования raw:
- "raw"    — значение как есть (стандартный контроллер)
- "low8"   — младший байт (температура Kingston/SandForce)
- "low16"  — младшие 2 байта
- "low20"  — младшие 20 бит (SandForce Power-On Hours)
- "low32"  — младшие 4 байта (SandForce критические атрибуты)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Профили контроллеров ───

VENDOR_PROFILES = [
    {
        "name": "SandForce SF-2281 (Kingston SKC300)",
        "match": {
            "model_contains": ["SKC300", "SF-2281", "SandForce"],
        },
        "decode": {
            # Power-On Hours: 20-битная маска
            9:   {"method": "low20", "name_en": "Power-On Hours", "name_ru": "Время работы"},
            # Температура: младший байт
            189: {"method": "low8", "name_en": "Temperature", "name_ru": "Температура"},
            194: {"method": "low8", "name_en": "Temperature", "name_ru": "Температура"},
            # Критические атрибуты: 32-битная маска
            5:   {"method": "low32"},
            196: {"method": "low32"},
            197: {"method": "low32"},
            198: {"method": "low32"},
            # Остальные packed-атрибуты: показываем raw, но decoded в tooltip
            1:   {"method": "low32"},
            13:  {"method": "low20"},
            195: {"method": "low32"},
            201: {"method": "low32"},
            204: {"method": "low32"},
        },
        "confidence": "high",
    },
    {
        "name": "Kingston A400/SA400 (Phison S11)",
        "match": {
            "model_contains": ["SA400", "A400"],
        },
        "decode": {
            # Стандартный контроллер — raw как есть
            9:   {"method": "raw"},
            194: {"method": "low8"},
        },
        "confidence": "high",
    },
    {
        "name": "Kingston NV2/NV1 (Phison E21T)",
        "match": {
            "model_contains": ["SNV2S", "SNV1S", "NV2", "NV1"],
        },
        "decode": {
            # NVMe — стандартный, без packed
        },
        "confidence": "high",
    },
    {
        "name": "Transcend MTS820/830 (Silicon Motion SM2258)",
        "match": {
            "model_contains": ["TS120GMTS", "TS240GMTS", "TS480GMTS", "TS960GMTS",
                               "MTS820", "MTS830"],
        },
        "decode": {
            9:   {"method": "raw"},
            194: {"method": "low8"},
        },
        "confidence": "medium",
    },
    {
        "name": "Intel SSD (generic)",
        "match": {
            "model_contains": ["INTEL SSD", "SSDSC2", "SSDPE"],
        },
        "decode": {
            9:   {"method": "raw"},
            194: {"method": "low8"},
        },
        "confidence": "medium",
    },
    {
        "name": "Samsung SSD (generic)",
        "match": {
            "model_contains": ["Samsung SSD", "SAMSUNG MZ"],
        },
        "decode": {
            9:   {"method": "raw"},
            194: {"method": "low8"},
        },
        "confidence": "high",
    },
    {
        "name": "SanDisk SSD (generic)",
        "match": {
            "model_contains": ["SanDisk SD", "SDSSDA", "SDSSD"],
        },
        "decode": {
            9:   {"method": "raw"},
            194: {"method": "low8"},
        },
        "confidence": "medium",
    },
]


# ─── API ───

def match_profile(model: str, firmware: str = "") -> Optional[dict]:
    """Найти подходящий профиль по модели и прошивке.

    Returns:
        dict профиля или None.
    """
    model_upper = model.upper().strip()
    fw_upper = firmware.upper().strip()

    for profile in VENDOR_PROFILES:
        match = profile["match"]

        # Проверка model_contains
        if "model_contains" in match:
            if any(pat.upper() in model_upper for pat in match["model_contains"]):
                logger.info(f"Vendor profile matched: {profile['name']}")
                return profile

        # Проверка firmware_contains
        if "firmware_contains" in match:
            if any(pat.upper() in fw_upper for pat in match["firmware_contains"]):
                logger.info(f"Vendor profile matched (fw): {profile['name']}")
                return profile

    logger.debug(f"No vendor profile for: {model}")
    return None


    # Универсальные правила — НЕ зависят от vendor profile
_DEFAULT_DECODE = {
    190: "low8",   # Airflow Temperature — ВСЕГДА low byte
    194: "low8",   # Temperature — ВСЕГДА low byte
}


def decode_raw(profile: Optional[dict], attr_id: int, raw_value: int) -> int:
    """Декодировать raw-значение атрибута через профиль.

    Для температуры (190, 194) — всегда low byte, даже без профиля.
    Для остальных без профиля — возвращает raw как есть.
    """
    # Сначала ищем в профиле
    if profile and "decode" in profile:
        rule = profile["decode"].get(attr_id)
        if rule:
            method = rule.get("method", "raw")
            return _apply_method(method, raw_value)

    # Дефолтные правила (температура и т.д.)
    default_method = _DEFAULT_DECODE.get(attr_id)
    if default_method:
        return _apply_method(default_method, raw_value)

    return raw_value


def _apply_method(method: str, raw_value: int) -> int:
    """Применить метод декодирования к raw-значению."""
    if method == "raw":
        return raw_value
    elif method == "low8":
        return raw_value & 0xFF
    elif method == "low16":
        return raw_value & 0xFFFF
    elif method == "low20":
        return raw_value & 0xFFFFF
    elif method == "low32":
        return raw_value & 0xFFFFFFFF
    else:
        return raw_value


def get_decoded_tooltip(profile: Optional[dict], attr_id: int, raw_value: int) -> str:
    """Получить строку с decoded-значением для tooltip.

    Returns: "" если декодирование не нужно, иначе "Decoded: N (method)"
    """
    if not profile:
        return ""

    rule = profile.get("decode", {}).get(attr_id)
    if not rule:
        return ""

    method = rule.get("method", "raw")
    if method == "raw":
        return ""

    decoded = decode_raw(profile, attr_id, raw_value)
    if decoded == raw_value:
        return ""

    return f"Decoded: {decoded:,} ({method})"
