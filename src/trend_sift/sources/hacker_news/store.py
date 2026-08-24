"""Hacker News persistence schema plus raw-feed and snapshot operations.

Enrichment, summary, and notification writes stay with their producing modules because
their SQL is coupled to fetching, prompt versions, and delivery semantics.
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
# Layer 0: archived raw JSON.
# --------------------------------------------------------------------------


def save_raw_feed(conn: sqlite3.Connection, feed: RawFeed) -> None:
    """Archive the latest raw response for a UTC date."""
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
    """Load archived JSON for parser replay."""
    row = conn.execute(
        "SELECT body_gz FROM hn_raw_feeds WHERE snapshot_date = ?", (snapshot_date,)
    ).fetchone()
    return gzip.decompress(row["body_gz"]).decode("utf-8") if row else None


# --------------------------------------------------------------------------
# Layer 1: immutable structured snapshots.
# --------------------------------------------------------------------------


def save_snapshots(conn: sqlite3.Connection, snaps: Iterable[StorySnapshot]) -> int:
    """Insert snapshots idempotently without replacing existing rows."""
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
    """Return stories observed before the requested snapshot date."""
    rows = conn.execute(
        "SELECT DISTINCT object_id FROM hn_snapshots WHERE snapshot_date < ?",
        (snapshot_date,),
    ).fetchall()
    return {r["object_id"] for r in rows}


def stories_on(conn: sqlite3.Connection, snapshot_date: str) -> list[sqlite3.Row]:
    """Return one UTC day's stories in rank order."""
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
