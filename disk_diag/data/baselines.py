"""Эталонные значения производительности по классам дисков.

Используется для сравнения результатов бенчмарка с типичными показателями.

ВАЖНО про QD1 vs QD32:
- `rand_4k_read_iops` / `rand_4k_write_iops` в этой утилите измеряются
  СИНХРОННО (QD1, очередь 1, цикл sleep-while-pending). Это latency test.
- Производители заявляют пиковые IOPS при QD32+ (асинхронно через NVMe SQ),
  что физически нельзя получить QD1 Python-циклом.
- Поэтому baseline IOPS приведены как QD1-ranges (1-50k для consumer NVMe),
  а пиковые QD32 цифры из datasheet хранятся отдельно как информация.
"""

from ..i18n import tr

BASELINES = {
    "sata_hdd": {
        "name_en": "SATA HDD",
        "name_ru": "SATA HDD",
        "seq_read": (80, 200),
        "seq_write": (80, 200),
        # HDD: QD1 random IOPS ограничены seek time (~7-10 ms) → 100-200 IOPS
        "rand_4k_read_iops": (50, 200),
        "rand_4k_write_iops": (50, 200),
        "qd32_peak_read_iops": 200,   # для справки; в HDD очередь не помогает
        "qd32_peak_write_iops": 200,
        "link_max_mbps": 600,
    },
    "sata_ssd": {
        "name_en": "SATA SSD (TLC/QLC)",
        "name_ru": "SATA SSD (TLC/QLC)",
        "seq_read": (400, 560),
        "seq_write": (300, 530),
        # QD1: типичный consumer SATA SSD даёт 8-20k IOPS на чтении,
        # 10-30k на записи (контроллер скрывает latency меньше чем NVMe).
        "rand_4k_read_iops": (8000, 20000),
        "rand_4k_write_iops": (10000, 30000),
        "qd32_peak_read_iops": 100000,
        "qd32_peak_write_iops": 90000,
        "link_max_mbps": 600,
    },
    "nvme_gen3": {
        "name_en": "NVMe Gen3 x4",
        "name_ru": "NVMe Gen3 x4",
        "seq_read": (1500, 3500),
        "seq_write": (1000, 3000),
        # QD1: NVMe contoller обычно показывает 12-50k IOPS QD1 (low latency).
        # QD32+ может выдать 300-600k, но это не для нашего движка.
        "rand_4k_read_iops": (12000, 50000),
        "rand_4k_write_iops": (15000, 60000),
        "qd32_peak_read_iops": 600000,
        "qd32_peak_write_iops": 500000,
        "link_max_mbps": 3940,
    },
    "nvme_gen4": {
        "name_en": "NVMe Gen4 x4",
        "name_ru": "NVMe Gen4 x4",
        "seq_read": (3500, 7400),
        "seq_write": (2500, 7000),
        # Gen4: QD1 random IOPS немного выше — лучшие латентности 15-80k.
        "rand_4k_read_iops": (15000, 80000),
        "rand_4k_write_iops": (20000, 100000),
        "qd32_peak_read_iops": 1000000,
        "qd32_peak_write_iops": 1000000,
        "link_max_mbps": 7880,
    },
    "usb_30": {
        "name_en": "USB 3.0",
        "name_ru": "USB 3.0",
        "seq_read": (200, 450),
        "seq_write": (150, 400),
        "rand_4k_read_iops": (3000, 15000),
        "rand_4k_write_iops": (3000, 15000),
        "qd32_peak_read_iops": 30000,
        "qd32_peak_write_iops": 30000,
        "link_max_mbps": 450,
    },
    "usb_32_gen2": {
        "name_en": "USB 3.2 Gen2",
        "name_ru": "USB 3.2 Gen2",
        "seq_read": (400, 1050),
        "seq_write": (300, 1000),
        "rand_4k_read_iops": (5000, 25000),
        "rand_4k_write_iops": (5000, 25000),
        "qd32_peak_read_iops": 50000,
        "qd32_peak_write_iops": 50000,
        "link_max_mbps": 1050,
    },
}


def detect_class(interface: str, seq_read_mbps: float = 0) -> str:
    """Определить класс диска по интерфейсу и скорости."""
    iface = interface.lower()
    if "usb" in iface:
        if seq_read_mbps > 500:
            return "usb_32_gen2"
        return "usb_30"
    elif "nvme" in iface:
        if seq_read_mbps > 4000:
            return "nvme_gen4"
        return "nvme_gen3"
    elif "sata" in iface or "ata" in iface:
        if seq_read_mbps > 250:
            return "sata_ssd"
        return "sata_hdd"
    return "sata_ssd"  # default


def compare_to_baseline(interface: str, seq_read: float = 0, seq_write: float = 0,
                        rand_4k_read_iops: float = 0, rand_4k_write_iops: float = 0) -> list[dict]:
    """Сравнить результаты бенчмарка с эталоном.

    Returns: список {"metric", "value", "range_min", "range_max", "pct_of_max",
                     "verdict", "class", "note"}.

    "note" — дополнительное пояснение (напр. "QD1 measurement, vendor specs QD32+").
    """
    cls = detect_class(interface, seq_read)
    baseline = BASELINES.get(cls)
    if not baseline:
        return []

    results = []

    def check(metric_en: str, metric_ru: str, value: float, range_key: str,
              note_en: str = "", note_ru: str = ""):
        if value <= 0:
            return
        rng = baseline.get(range_key)
        if not rng:
            return
        rng_min, rng_max = rng
        pct = value / rng_max * 100 if rng_max > 0 else 0
        if value >= rng_min:
            verdict = "pass"
        elif value >= rng_min * 0.6:
            verdict = "warn"
        else:
            verdict = "fail"
        results.append({
            "metric": tr(metric_en, metric_ru),
            "value": value,
            "range_min": rng_min,
            "range_max": rng_max,
            "pct_of_max": round(pct, 1),
            "verdict": verdict,
            "class": tr(baseline["name_en"], baseline["name_ru"]),
            "note": tr(note_en, note_ru),
        })

    check("Seq Read", "Послед. чтение", seq_read, "seq_read")
    check("Seq Write", "Послед. запись", seq_write, "seq_write")
    # IOPS — наш QD1 синхронный тест. Сравниваем с QD1 диапазонами.
    # Если хочется сравнить с маркетинговыми QD32 — в `qd32_peak_*` для справки.
    check("4K Read IOPS (QD1)", "4K чтение IOPS (QD1)",
          rand_4k_read_iops, "rand_4k_read_iops",
          "QD1 latency test — vendor specs typically quote QD32 peaks",
          "QD1 latency-тест — спеки производителя обычно для QD32")
    check("4K Write IOPS (QD1)", "4K запись IOPS (QD1)",
          rand_4k_write_iops, "rand_4k_write_iops",
          "QD1 latency test — vendor specs typically quote QD32 peaks",
          "QD1 latency-тест — спеки производителя обычно для QD32")

    return results
