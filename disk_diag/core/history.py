"""История тестов по серийному номеру диска (SQLite)."""

import json
import logging
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    """Путь к базе данных — рядом с exe или в корне проекта."""
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), "disk_history.db")
    else:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "disk_history.db")


def _get_db() -> sqlite3.Connection:
    """Открыть/создать базу данных."""
    db = sqlite3.connect(_get_db_path())
    db.execute("""
        CREATE TABLE IF NOT EXISTS test_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            serial TEXT NOT NULL,
            model TEXT NOT NULL,
            tool_version TEXT,
            health_score INTEGER,
            temperature INTEGER,
            tbw_consumed_tb REAL,
            power_on_hours INTEGER,
            seq_read_mbps REAL,
            seq_write_mbps REAL,
            random_4k_iops REAL,
            waf REAL,
            penalties TEXT,
            notes TEXT,
            attributes_json TEXT
        )
    """)
    # Миграция: attributes_json появился в v2.7.0 (trend-история). Для БД,
    # созданных раньше, добавляем колонку — без неё снимки атрибутов негде хранить.
    cols = [r[1] for r in db.execute("PRAGMA table_info(test_history)").fetchall()]
    if "attributes_json" not in cols:
        db.execute("ALTER TABLE test_history ADD COLUMN attributes_json TEXT")
    db.commit()
    return db


def save_test(serial: str, model: str, version: str,
              health_score: int = -1, temperature: int = -1,
              tbw_consumed_tb: float = -1, power_on_hours: int = -1,
              seq_read_mbps: float = 0, seq_write_mbps: float = 0,
              random_4k_iops: float = 0, waf: float = -1,
              penalties: list = None, notes: str = "",
              attributes: dict = None):
    """Сохранить результат теста в историю.

    attributes — снимок ключевых значений для trend-анализа: для ATA
    {str(attr_id): raw_value}, для NVMe {field_name: value}. Сравнивается с
    предыдущим снимком (get_previous_snapshot) при следующем чтении.
    """
    try:
        # closing() гарантирует db.close() даже при ошибке SQL —
        # без него соединения копились в долгоживущем GUI-процессе
        with closing(_get_db()) as db:
            db.execute(
                """INSERT INTO test_history
                   (timestamp, serial, model, tool_version,
                    health_score, temperature, tbw_consumed_tb, power_on_hours,
                    seq_read_mbps, seq_write_mbps, random_4k_iops, waf,
                    penalties, notes, attributes_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(),
                    serial, model, version,
                    health_score, temperature, tbw_consumed_tb, power_on_hours,
                    seq_read_mbps, seq_write_mbps, random_4k_iops, waf,
                    json.dumps(penalties or [], ensure_ascii=False),
                    notes,
                    json.dumps(attributes) if attributes else None,
                )
            )
            db.commit()
        logger.info(f"History saved for {model} ({serial[:16]})")
    except Exception as e:
        logger.warning(f"Cannot save history: {e}")


def get_previous_snapshot(serial: str) -> Optional[tuple[str, dict]]:
    """Вернуть самый свежий снимок атрибутов (timestamp, {key: value}) для диска.

    ВАЖНО: вызывать ДО сохранения нового снимка — иначе «предыдущим» окажется
    только что записанный текущий. Возвращает None, если истории со снимком нет
    (первое наблюдение этого диска).
    """
    if not serial:
        return None
    try:
        with closing(_get_db()) as db:
            cursor = db.execute(
                """SELECT timestamp, attributes_json
                   FROM test_history
                   WHERE serial = ? AND attributes_json IS NOT NULL
                   ORDER BY timestamp DESC
                   LIMIT 1""",
                (serial,)
            )
            row = cursor.fetchone()
        if not row:
            return None
        timestamp, attrs_json = row
        attrs = json.loads(attrs_json)
        if not isinstance(attrs, dict):
            return None
        return (timestamp, attrs)
    except Exception as e:
        logger.warning(f"Cannot read previous snapshot: {e}")
        return None


def get_history(serial: str) -> list[dict]:
    """Получить историю тестов для диска по серийному номеру."""
    try:
        with closing(_get_db()) as db:
            cursor = db.execute(
                """SELECT timestamp, tool_version,
                          health_score, temperature, tbw_consumed_tb, power_on_hours,
                          seq_read_mbps, seq_write_mbps, random_4k_iops, waf,
                          penalties, notes
                   FROM test_history
                   WHERE serial = ?
                   ORDER BY timestamp DESC
                   LIMIT 50""",
                (serial,)
            )
            columns = [d[0] for d in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        for row in rows:
            try:
                row["penalties"] = json.loads(row["penalties"]) if row["penalties"] else []
            except Exception:
                row["penalties"] = []

        return rows
    except Exception as e:
        logger.warning(f"Cannot read history: {e}")
        return []


def get_all_disks() -> list[dict]:
    """Получить список всех дисков в истории."""
    try:
        with closing(_get_db()) as db:
            cursor = db.execute(
                """SELECT serial, model,
                          COUNT(*) as test_count,
                          MAX(timestamp) as last_test,
                          MIN(health_score) as min_score,
                          MAX(health_score) as max_score
                   FROM test_history
                   GROUP BY serial
                   ORDER BY last_test DESC"""
            )
            columns = [d[0] for d in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return rows
    except Exception as e:
        logger.warning(f"Cannot read disk list: {e}")
        return []
