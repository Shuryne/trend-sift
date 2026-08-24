"""Hacker News 的持久化：hn_* 五张表。

结构对齐 github/store.py 的分层，但字段是 HN 自己的：

  hn_raw_feeds      第 0 层  Algolia 原始 JSON 归档，可用于重放解析
  hn_snapshots      第 1 层  结构化快照，只 INSERT 永不 UPDATE
  hn_stories        第 2 层  正文/自述内容，按 enriched_at 判断是否过期
  hn_summaries      第 3 层  LLM 中文摘要，按 prompt_version 版本化
  hn_notifications  第 4 层  推送记录，推送幂等的依据

主键是 object_id（HN item id）而不是 GitHub 那边的 full_name —— 这是两个
数据源不共用表结构的根本原因。

与 github/store.py 同样的分工：第 2/3/4 层的读写在各自的生产者模块
（enrich.py / summarize.py / notify.py），本模块只持有 DDL 和第 0/1 层。
"""

import gzip
import sqlite3
from collections.abc import Iterable

from .models import RawFeed, StorySnapshot

HN_SCHEMA = """
CREATE TABLE IF NOT EXISTS hn_raw_feeds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT    NOT NULL,
    url           TEXT    NOT NULL,
    http_status   INTEGER NOT NULL,
    body_gz       BLOB    NOT NULL,
    fetched_at    TEXT    NOT NULL,
    UNIQUE(snapshot_date)
);

CREATE TABLE IF NOT EXISTS hn_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT    NOT NULL,
    rank          INTEGER NOT NULL,
    object_id     TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    url           TEXT,
    story_text    TEXT,
    author        TEXT,
    points        INTEGER,
    num_comments  INTEGER,
    created_at_i  INTEGER,
    fetched_at    TEXT    NOT NULL,
    UNIQUE(snapshot_date, object_id)
);
CREATE INDEX IF NOT EXISTS idx_hn_snap_story ON hn_snapshots(object_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_hn_snap_date  ON hn_snapshots(snapshot_date, rank);

CREATE TABLE IF NOT EXISTS hn_stories (
    object_id    TEXT PRIMARY KEY,
    story_text   TEXT,
    article_head TEXT,
    site         TEXT,
    enriched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hn_summaries (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id      TEXT NOT NULL,
    summary_zh     TEXT,
    prompt_version TEXT NOT NULL,
    model          TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(object_id, prompt_version)
);

CREATE TABLE IF NOT EXISTS hn_notifications (
    object_id         TEXT PRIMARY KEY,
    first_notified_at TEXT NOT NULL,
    snapshot_date     TEXT NOT NULL
);
"""


# --------------------------------------------------------------------------
# 第 0 层：原始 JSON
# --------------------------------------------------------------------------


def save_raw_feed(conn: sqlite3.Connection, feed: RawFeed) -> None:
    """存档原始响应。同一天重复抓取时覆盖，保留最后一次。"""
    conn.execute(
        """
        INSERT INTO hn_raw_feeds (snapshot_date, url, http_status, body_gz, fetched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date) DO UPDATE SET
            url=excluded.url,
            http_status=excluded.http_status,
            body_gz=excluded.body_gz,
            fetched_at=excluded.fetched_at
        """,
        (
            feed.snapshot_date,
            feed.url,
            feed.http_status,
            gzip.compress(feed.body.encode("utf-8")),
            feed.fetched_at,
        ),
    )


def load_raw_feed(conn: sqlite3.Connection, snapshot_date: str) -> str | None:
    """取回历史 JSON，用于重放解析。"""
    row = conn.execute(
        "SELECT body_gz FROM hn_raw_feeds WHERE snapshot_date = ?", (snapshot_date,)
    ).fetchone()
    return gzip.decompress(row["body_gz"]).decode("utf-8") if row else None


# --------------------------------------------------------------------------
# 第 1 层：结构化快照
# --------------------------------------------------------------------------


def save_snapshots(conn: sqlite3.Connection, snaps: Iterable[StorySnapshot]) -> int:
    """写入快照。同日同帖已存在则跳过 —— 这让重跑天然幂等。"""
    rows = [
        (
            s.snapshot_date,
            s.rank,
            s.object_id,
            s.title,
            s.url,
            s.story_text,
            s.author,
            s.points,
            s.num_comments,
            s.created_at_i,
            s.fetched_at,
        )
        for s in snaps
    ]
    if not rows:
        return 0
    cur = conn.executemany(
        """
        INSERT OR IGNORE INTO hn_snapshots
            (snapshot_date, rank, object_id, title, url, story_text, author,
             points, num_comments, created_at_i, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return cur.rowcount


def stories_seen_before(conn: sqlite3.Connection, snapshot_date: str) -> set[str]:
    """在 snapshot_date 之前就出现过的帖子，用于识别「首次上榜」。"""
    rows = conn.execute(
        "SELECT DISTINCT object_id FROM hn_snapshots WHERE snapshot_date < ?",
        (snapshot_date,),
    ).fetchall()
    return {r["object_id"] for r in rows}


def stories_on(conn: sqlite3.Connection, snapshot_date: str) -> list[sqlite3.Row]:
    """当日榜单，按名次。HN 没有 GitHub 那种多榜并存，不需要去重聚合。"""
    return conn.execute(
        """
        SELECT object_id, rank, title, url, story_text, author,
               points, num_comments, created_at_i
        FROM hn_snapshots
        WHERE snapshot_date = ?
        ORDER BY rank
        """,
        (snapshot_date,),
    ).fetchall()
