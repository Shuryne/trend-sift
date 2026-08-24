"""GitHub Trending 的持久化：gh_* 五张表的 DDL 与第 0/1 层读写。

  gh_raw_pages      第 0 层  原始 HTML 归档，gzip 存储，可用于重放解析
  gh_snapshots      第 1 层  结构化快照，只 INSERT 永不 UPDATE，时序数据的来源
  gh_repos          第 2 层  仓库元信息，缓慢变化，按 enriched_at 判断是否过期
  gh_summaries      第 3 层  LLM 中文摘要，按 prompt_version 版本化，可重算
  gh_notifications  第 4 层  推送记录，推送幂等的依据

第 2/3/4 层的读写留在各自的生产者模块（enrich.py / summarize.py / notify.py）：
它们的 SQL 与写入时机同 API 调用、prompt 版本、推送动作强耦合，拆到这里
反而要来回翻两个文件。本模块只持有 DDL 和没有单一生产者的第 0/1 层。
"""

import gzip
import sqlite3
from collections.abc import Iterable

from .config import Period
from .models import RawPage, RepoSnapshot

GH_SCHEMA = """
CREATE TABLE IF NOT EXISTS gh_raw_pages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT    NOT NULL,
    period        TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    http_status   INTEGER NOT NULL,
    html_gz       BLOB    NOT NULL,
    fetched_at    TEXT    NOT NULL,
    UNIQUE(snapshot_date, period)
);

CREATE TABLE IF NOT EXISTS gh_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT    NOT NULL,
    period        TEXT    NOT NULL,
    rank          INTEGER NOT NULL,
    full_name     TEXT    NOT NULL,
    description   TEXT,
    language      TEXT,
    stars         INTEGER,
    forks         INTEGER,
    stars_period  INTEGER,
    fetched_at    TEXT    NOT NULL,
    UNIQUE(snapshot_date, period, full_name)
);
CREATE INDEX IF NOT EXISTS idx_gh_snap_repo ON gh_snapshots(full_name, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_gh_snap_date ON gh_snapshots(snapshot_date, period, rank);

CREATE TABLE IF NOT EXISTS gh_repos (
    full_name   TEXT PRIMARY KEY,
    owner       TEXT,
    topics      TEXT,
    readme_head TEXT,
    homepage    TEXT,
    license     TEXT,
    created_at  TEXT,
    enriched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gh_summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name      TEXT NOT NULL,
    summary_zh     TEXT,
    prompt_version TEXT NOT NULL,
    model          TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(full_name, prompt_version)
);

CREATE TABLE IF NOT EXISTS gh_notifications (
    full_name         TEXT PRIMARY KEY,
    first_notified_at TEXT NOT NULL,
    snapshot_date     TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------
# 第 0 层：原始 HTML
# --------------------------------------------------------------------------


def save_raw_page(conn: sqlite3.Connection, page: RawPage) -> None:
    """存档原始 HTML。同一天同一榜单重复抓取时覆盖，保留最后一次。"""
    conn.execute(
        """
        INSERT INTO gh_raw_pages (snapshot_date, period, url, http_status, html_gz, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, period) DO UPDATE SET
            url=excluded.url,
            http_status=excluded.http_status,
            html_gz=excluded.html_gz,
            fetched_at=excluded.fetched_at
        """,
        (
            page.snapshot_date,
            page.period,
            page.url,
            page.http_status,
            gzip.compress(page.html.encode("utf-8")),
            page.fetched_at,
        ),
    )


def load_raw_page(conn: sqlite3.Connection, snapshot_date: str, period: Period) -> str | None:
    """取回历史 HTML，用于重放解析。"""
    row = conn.execute(
        "SELECT html_gz FROM gh_raw_pages WHERE snapshot_date = ? AND period = ?",
        (snapshot_date, period),
    ).fetchone()
    return gzip.decompress(row["html_gz"]).decode("utf-8") if row else None


# --------------------------------------------------------------------------
# 第 1 层：结构化快照
# --------------------------------------------------------------------------


def save_snapshots(conn: sqlite3.Connection, snaps: Iterable[RepoSnapshot]) -> int:
    """写入快照。同日同榜同仓库已存在则跳过 —— 这让重跑天然幂等。"""
    rows = [
        (
            s.snapshot_date,
            s.period,
            s.rank,
            s.full_name,
            s.description,
            s.language,
            s.stars,
            s.forks,
            s.stars_period,
            s.fetched_at,
        )
        for s in snaps
    ]
    if not rows:
        return 0
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO gh_snapshots
            (snapshot_date, period, rank, full_name, description,
             language, stars, forks, stars_period, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount


def repos_seen_before(conn: sqlite3.Connection, snapshot_date: str) -> set[str]:
    """在 snapshot_date 之前就出现过的仓库，用于识别「首次上榜」。"""
    rows = conn.execute(
        "SELECT DISTINCT full_name FROM gh_snapshots WHERE snapshot_date < ?",
        (snapshot_date,),
    ).fetchall()
    return {r["full_name"] for r in rows}


def distinct_repos_on(conn: sqlite3.Connection, snapshot_date: str) -> list[sqlite3.Row]:
    """当日去重后的仓库列表。同一仓库出现在多个榜时，取排名最靠前的那条。

    仅供富化 / 摘要 / show 命令使用 —— 它们按仓库去重处理，与周期无关。
    推送走的是 notify.items_by_period，那里必须保留周期维度。
    """
    return conn.execute(
        """
        SELECT full_name, description, language, stars, forks,
               MIN(rank) AS best_rank,
               MAX(stars_period) AS stars_period,
               GROUP_CONCAT(period) AS periods
        FROM gh_snapshots
        WHERE snapshot_date = ?
        GROUP BY full_name
        ORDER BY best_rank
        """,
        (snapshot_date,),
    ).fetchall()


def days_on_board(conn: sqlite3.Connection, full_name: str) -> int:
    row = conn.execute(
        "SELECT COUNT(DISTINCT snapshot_date) AS n FROM gh_snapshots WHERE full_name = ?",
        (full_name,),
    ).fetchone()
    return row["n"] if row else 0
