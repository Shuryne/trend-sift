"""SQLite 连接管理与跨数据源共用的表。

分层的动机（原始数据与派生数据严格分离）见 README「数据库设计」。
每个数据源有自己的一组表（`github/store.py` 的 `gh_*`、`hn/store.py` 的
`hn_*`），本模块只持有：

  - 连接与事务管理（connect / WAL / 外键）
  - runs 表：每次执行的审计记录，与数据源无关
  - 时间工具：全项目统一用 CST，避免各处自己 datetime.now()

The schema entry point combines source-specific DDL without making this module
depend on either source at runtime.
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
    # WAL 让读写不互相阻塞；外键约束默认关闭，显式打开
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
# runs：执行审计，所有数据源共用
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
