from dataclasses import replace
from pathlib import Path

import pytest

from trend_sift.core.schema import init_db
from trend_sift.core.store import connect, finish_run, start_run
from trend_sift.sources.github.models import RawPage, RepoSnapshot
from trend_sift.sources.github.store import (
    days_on_board,
    distinct_repos_on,
    load_raw_page,
    repos_seen_before,
    save_raw_page,
    save_snapshots,
)
from trend_sift.sources.hacker_news.models import RawFeed, StorySnapshot
from trend_sift.sources.hacker_news.store import (
    load_raw_feed,
    save_raw_feed,
    stories_on,
    stories_seen_before,
)
from trend_sift.sources.hacker_news.store import (
    save_snapshots as save_hn_snapshots,
)


def test_schema_and_run_audit(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "test.db"
    init_db(db)
    with connect(db) as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master")}
        assert {"runs", "gh_snapshots", "hn_snapshots"} <= tables
        run_id = start_run(conn, "github")
        finish_run(conn, run_id, "ok", {"fetched": 2})
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        assert row["status"] == "ok"
        assert '"fetched": 2' in row["stats_json"]


def test_connect_rolls_back_on_error(tmp_path: Path) -> None:
    db = tmp_path / "rollback.db"
    init_db(db)
    with pytest.raises(RuntimeError), connect(db) as conn:
        start_run(conn, "github")
        raise RuntimeError("stop")
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0


def test_github_archive_and_snapshots_are_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "github.db"
    init_db(db)
    raw = RawPage("2026-08-23", "daily", "https://example", 200, "old", "then")
    snapshot = RepoSnapshot(
        "2026-08-23", "daily", 1, "openai/codex", "desc", "Rust", 10, 2, 5, "then"
    )
    with connect(db) as conn:
        assert load_raw_page(conn, "2026-08-23", "daily") is None
        save_raw_page(conn, raw)
        save_raw_page(conn, replace(raw, html="new"))
        assert load_raw_page(conn, "2026-08-23", "daily") == "new"
        assert save_snapshots(conn, []) == 0
        assert save_snapshots(conn, [snapshot]) == 1
        assert save_snapshots(conn, [snapshot]) == 0
        assert distinct_repos_on(conn, "2026-08-23")[0]["full_name"] == "openai/codex"
        assert days_on_board(conn, "openai/codex") == 1
        assert repos_seen_before(conn, "2026-08-24") == {"openai/codex"}


def test_hn_archive_and_snapshots_are_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "hn.db"
    init_db(db)
    raw = RawFeed("2026-08-22", "https://example", 200, '{"hits": []}', "then")
    snapshot = StorySnapshot(
        "2026-08-22", 1, "42", "Story", None, "text", "alice", 300, 20, 1, "then"
    )
    with connect(db) as conn:
        assert load_raw_feed(conn, "2026-08-22") is None
        save_raw_feed(conn, raw)
        assert load_raw_feed(conn, "2026-08-22") == raw.body
        assert save_hn_snapshots(conn, []) == 0
        assert save_hn_snapshots(conn, [snapshot]) == 1
        assert save_hn_snapshots(conn, [snapshot]) == 0
        assert stories_on(conn, "2026-08-22")[0]["object_id"] == "42"
        assert stories_seen_before(conn, "2026-08-23") == {"42"}
