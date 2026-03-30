"""История тестов по серийному номеру диска (SQLite)."""

import json
import logging
import os
import sqlite3
import sys
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
            notes TEXT
        )
    """)
    db.commit()
    return db


def save_test(serial: str, model: str, version: str,
              health_score: int = -1, temperature: int = -1,
              tbw_consumed_tb: float = -1, power_on_hours: int = -1,
              seq_read_mbps: float = 0, seq_write_mbps: float = 0,
              random_4k_iops: float = 0, waf: float = -1,
              penalties: list = None, notes: str = ""):
    """Сохранить результат теста в историю."""
    try:
        db = _get_db()
        db.execute(
            """INSERT INTO test_history
               (timestamp, serial, model, tool_version,
                health_score, temperature, tbw_consumed_tb, power_on_hours,
                seq_read_mbps, seq_write_mbps, random_4k_iops, waf,
                penalties, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                serial, model, version,
                health_score, temperature, tbw_consumed_tb, power_on_hours,
                seq_read_mbps, seq_write_mbps, random_4k_iops, waf,
                json.dumps(penalties or [], ensure_ascii=False),
                notes,
            )
        )
        db.commit()
        db.close()
        logger.info(f"History saved for {model} ({serial[:16]})")
    except Exception as e:
        logger.warning(f"Cannot save history: {e}")


def get_history(serial: str) -> list[dict]:
    """Получить историю тестов для диска по серийному номеру."""
    try:
        db = _get_db()
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
        db.close()

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
        db = _get_db()
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
        db.close()
        return rows
    except Exception as e:
        logger.warning(f"Cannot read disk list: {e}")
        return []
