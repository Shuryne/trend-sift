"""GitHub persistence schema plus raw-page and snapshot operations.

Enrichment, summary, and notification writes stay with their producing modules because
their SQL is coupled to API calls, prompt versions, and delivery semantics.
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
# Layer 0: archived raw HTML.
# --------------------------------------------------------------------------


def save_raw_page(conn: sqlite3.Connection, page: RawPage) -> None:
    """Archive raw HTML, retaining the latest response for a period and date."""
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
    """Load archived HTML for parser replay."""
    row = conn.execute(
        "SELECT html_gz FROM gh_raw_pages WHERE snapshot_date = ? AND period = ?",
        (snapshot_date, period),
    ).fetchone()
    return gzip.decompress(row["html_gz"]).decode("utf-8") if row else None


# --------------------------------------------------------------------------
# Layer 1: immutable structured snapshots.
# --------------------------------------------------------------------------


def save_snapshots(conn: sqlite3.Connection, snaps: Iterable[RepoSnapshot]) -> int:
    """Insert snapshots idempotently without replacing existing rows."""
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
    """Return repositories observed before the requested snapshot date."""
    rows = conn.execute(
        "SELECT DISTINCT full_name FROM gh_snapshots WHERE snapshot_date < ?",
        (snapshot_date,),
    ).fetchall()
    return {r["full_name"] for r in rows}


def distinct_repos_on(conn: sqlite3.Connection, snapshot_date: str) -> list[sqlite3.Row]:
    """Return repositories deduplicated across periods using their best rank.

    Enrichment and summaries are repository-level; notifications retain period detail.
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
