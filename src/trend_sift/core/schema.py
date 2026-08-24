"""Create the fresh trend-sift SQLite schema."""

from pathlib import Path

from ..sources.github.store import GH_SCHEMA
from ..sources.hacker_news.store import HN_SCHEMA
from .store import COMMON_SCHEMA, connect


def init_db(db_path: Path | None = None) -> None:
    """Create all tables and indexes idempotently."""
    with connect(db_path) as conn:
        conn.executescript(COMMON_SCHEMA + GH_SCHEMA + HN_SCHEMA)
