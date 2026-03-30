"""Эталонные значения производительности по классам дисков.

Используется для сравнения результатов бенчмарка с типичными показателями.
"""

from ..i18n import tr

# (seq_read_min, seq_read_max, seq_write_min, seq_write_max,
#  rand_4k_read_min, rand_4k_read_max, rand_4k_write_min, rand_4k_write_max)
# Все скорости в MB/s, IOPS для random

BASELINES = {
    "sata_hdd": {
        "name_en": "SATA HDD",
        "name_ru": "SATA HDD",
        "seq_read": (80, 200),
        "seq_write": (80, 200),
        "rand_4k_read_iops": (50, 200),
        "rand_4k_write_iops": (50, 200),
        "link_max_mbps": 600,
    },
    "sata_ssd": {
        "name_en": "SATA SSD (TLC/QLC)",
        "name_ru": "SATA SSD (TLC/QLC)",
        "seq_read": (400, 560),
        "seq_write": (300, 530),
        "rand_4k_read_iops": (8000, 100000),
        "rand_4k_write_iops": (20000, 90000),
        "link_max_mbps": 600,
    },
    "nvme_gen3": {
        "name_en": "NVMe Gen3 x4",
        "name_ru": "NVMe Gen3 x4",
        "seq_read": (1500, 3500),
        "seq_write": (1000, 3000),
        "rand_4k_read_iops": (12000, 600000),
        "rand_4k_write_iops": (50000, 500000),
        "link_max_mbps": 3940,
    },
    "nvme_gen4": {
        "name_en": "NVMe Gen4 x4",
        "name_ru": "NVMe Gen4 x4",
        "seq_read": (3500, 7400),
        "seq_write": (2500, 7000),
        "rand_4k_read_iops": (15000, 1000000),
        "rand_4k_write_iops": (100000, 1000000),
        "link_max_mbps": 7880,
    },
    "usb_30": {
        "name_en": "USB 3.0",
        "name_ru": "USB 3.0",
        "seq_read": (200, 450),
        "seq_write": (150, 400),
        "rand_4k_read_iops": (3000, 30000),
        "rand_4k_write_iops": (3000, 30000),
        "link_max_mbps": 450,
    },
    "usb_32_gen2": {
        "name_en": "USB 3.2 Gen2",
        "name_ru": "USB 3.2 Gen2",
        "seq_read": (400, 1050),
        "seq_write": (300, 1000),
        "rand_4k_read_iops": (5000, 50000),
        "rand_4k_write_iops": (5000, 50000),
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

    Returns: список {"metric", "value", "range_min", "range_max", "pct_of_max", "verdict"}
    """
    cls = detect_class(interface, seq_read)
    baseline = BASELINES.get(cls)
    if not baseline:
        return []

    results = []

    def check(metric_en: str, metric_ru: str, value: float, range_key: str):
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
        })

    check("Seq Read", "Послед. чтение", seq_read, "seq_read")
    check("Seq Write", "Послед. запись", seq_write, "seq_write")
    check("4K Read IOPS", "4K чтение IOPS", rand_4k_read_iops, "rand_4k_read_iops")
    check("4K Write IOPS", "4K запись IOPS", rand_4k_write_iops, "rand_4k_write_iops")

    return results
