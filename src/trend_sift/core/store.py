"""Shared SQLite connection management, run auditing, and time helpers.

Each source owns its tables. This module provides transactions, WAL setup, the
source-independent run audit table, and consistent application timestamps.
"""

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import CST, settings

COMMON_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,
    stats_json  TEXT,
    error       TEXT
);
"""


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


@contextmanager
def connect(db_path: Path | None = None) -> Generator[sqlite3.Connection]:
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # WAL improves concurrency; foreign keys must be enabled explicitly.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Execution audit shared by all data sources.
# --------------------------------------------------------------------------


def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute("INSERT INTO runs (source, started_at) VALUES (?, ?)", (source, now_iso()))
    assert cur.lastrowid is not None
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    stats: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, stats_json = ?, error = ? WHERE id = ?",
        (now_iso(), status, json.dumps(stats or {}, ensure_ascii=False), error, run_id),
    )
