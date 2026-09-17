"""Exercise the public read API against isolated archive databases."""

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from trend_sift.api.app import create_app
from trend_sift.core.config import settings
from trend_sift.core.store import connect


@pytest.fixture(autouse=True)
def disable_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "schedule_enabled", False)


def test_empty_database_and_static_site(tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>Trend Sift</html>")
    (web / "assets" / "app.js").write_text("console.log('ready')")
    with TestClient(create_app(tmp_path / "empty.db", web)) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/github").json() == {
            "date": None,
            "dates": [],
            "updated_at": None,
            "items": [],
        }
        assert client.get("/api/hacker-news").json()["items"] == []
        assert client.get("/").status_code == 200
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/api/missing").status_code == 404
        assert client.get("/.env").status_code == 404
        assert client.get("/api/github?period=bad").status_code == 422
        assert client.get("/api/github?date=2026-99-99").status_code == 422
        assert client.get("/api/hacker-news?date=invalid").status_code == 422


def test_archive_dates_periods_summaries_and_no_writes(tmp_path: Path) -> None:
    db = tmp_path / "archive.db"
    with TestClient(create_app(db, tmp_path / "missing")) as client:
        with connect(db) as conn:
            conn.executemany(
                "INSERT INTO gh_snapshots "
                "(snapshot_date,period,rank,full_name,fetched_at) VALUES (?,?,?,?,?)",
                [
                    ("2026-09-16", "daily", 2, "team/two", "2026-09-16T09:00:00+08:00"),
                    ("2026-09-16", "daily", 1, "team/one", "2026-09-16T09:00:00+08:00"),
                    ("2026-09-15", "daily", 1, "team/old", "2026-09-15T09:00:00+08:00"),
                    ("2026-09-14", "weekly", 1, "team/week", "2026-09-14T09:00:00+08:00"),
                ],
            )
            conn.executemany(
                "INSERT INTO gh_summaries "
                "(full_name,summary_zh,prompt_version,created_at) VALUES (?,?,?,?)",
                [
                    ("team/one", "旧摘要", "v1", "2026-09-15"),
                    ("team/one", "最新摘要", "v2", "2026-09-16"),
                    ("team/one", "", "v3", "2026-09-17"),
                ],
            )
            conn.execute(
                "INSERT INTO hn_snapshots "
                "(snapshot_date,rank,object_id,title,url,fetched_at) VALUES (?,?,?,?,?,?)",
                ("2026-09-14", 1, "123", "A story", "https://example.com", "2026-09-16"),
            )
            conn.executemany(
                "INSERT INTO hn_summaries "
                "(object_id,summary_zh,prompt_version,created_at) VALUES (?,?,?,?)",
                [("123", "旧闻", "v1", "2026-09-15"), ("123", "新摘要", "v2", "2026-09-16")],
            )
            before = list(conn.iterdump())
        result = client.get("/api/github").json()
        assert result["date"] == "2026-09-16"
        assert result["dates"] == ["2026-09-16", "2026-09-15"]
        assert [r["full_name"] for r in result["items"]] == ["team/one", "team/two"]
        assert result["items"][0]["summary_zh"] == "最新摘要"
        assert result["items"][1]["summary_zh"] is None
        assert "fetched_at" not in result["items"][0]
        assert (
            client.get("/api/github?date=2026-09-15").json()["items"][0]["full_name"] == "team/old"
        )
        assert client.get("/api/github?period=weekly").json()["date"] == "2026-09-14"
        assert client.get("/api/github?date=2020-01-01").json()["items"] == []
        hn = client.get("/api/hacker-news").json()
        assert hn["date"] == "2026-09-14"
        assert hn["items"][0]["summary_zh"] == "新摘要"
        assert client.get("/api/hacker-news?date=2020-01-01").json()["items"] == []
        assert client.get("/").status_code == 503
        with connect(db) as verify:
            assert list(verify.iterdump()) == before
            assert verify.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_github_new_and_days_use_only_history_through_selected_date(tmp_path: Path) -> None:
    db = tmp_path / "history.db"
    with TestClient(create_app(db)) as client:
        with connect(db) as conn:
            conn.executemany(
                "INSERT INTO gh_snapshots "
                "(snapshot_date,period,rank,full_name,fetched_at) VALUES (?,?,?,?,?)",
                [
                    ("2026-09-10", "weekly", 1, "team/returning", "2026-09-10"),
                    ("2026-09-10", "monthly", 1, "team/returning", "2026-09-10"),
                    ("2026-09-14", "daily", 1, "team/returning", "2026-09-14"),
                    ("2026-09-14", "weekly", 1, "team/returning", "2026-09-14"),
                    ("2026-09-14", "daily", 2, "team/new", "2026-09-14"),
                    ("2026-09-14", "weekly", 2, "team/new", "2026-09-14"),
                    ("2026-09-16", "daily", 1, "team/returning", "2026-09-16"),
                    ("2026-09-16", "daily", 2, "team/new", "2026-09-16"),
                ],
            )
        for period in ("daily", "weekly"):
            old, new = client.get(f"/api/github?date=2026-09-14&period={period}").json()["items"]
            assert old["is_new"] is False
            assert old["days_on_board"] == 2
            assert old["first_seen"] == "2026-09-10"
            assert new["is_new"] is True
            assert new["days_on_board"] == 1
            assert new["first_seen"] == "2026-09-14"
        first = client.get("/api/github?date=2026-09-10&period=weekly").json()["items"][0]
        assert first["is_new"] is True
        assert first["days_on_board"] == 1
        latest = client.get("/api/github").json()["items"]
        assert [item["days_on_board"] for item in latest] == [3, 2]
        assert all(not item["is_new"] for item in latest)


def test_scheduler_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trend_sift.api import app as api_module
    from trend_sift.cli import commands

    calls: list[str] = []

    async def loop(schedule_time: str, timezone: str) -> None:
        calls.extend([schedule_time, timezone])
        try:
            await asyncio.Future()
        finally:
            calls.append("cancelled")

    monkeypatch.setattr(api_module, "daily_loop", loop)
    monkeypatch.setattr(commands, "setup_logging", Mock())
    with TestClient(create_app(tmp_path / "test.db", schedule_enabled=True)) as client:
        assert client.get("/api/health").status_code == 200
        assert calls == [settings.schedule_time, settings.schedule_timezone]
    assert calls[-1] == "cancelled"


@pytest.mark.parametrize(
    ("lag_days", "edition", "content"),
    [
        (2, "2026-09-17", "2026-09-15"),
        (2, "2026-01-01", "2025-12-30"),
        (0, "2026-09-17", "2026-09-17"),
        (3, "2026-09-17", "2026-09-14"),
    ],
)
def test_hn_edition_dates_map_to_content_dates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lag_days: int,
    edition: str,
    content: str,
) -> None:
    monkeypatch.setattr(settings, "hn_lag_days", lag_days)
    db = tmp_path / "edition.db"
    with TestClient(create_app(db)) as client:
        with connect(db) as conn:
            conn.execute(
                "INSERT INTO hn_snapshots "
                "(snapshot_date,rank,object_id,title,fetched_at) VALUES (?,?,?,?,?)",
                (content, 1, "123", "Daily HN story", edition),
            )
        latest = client.get("/api/hacker-news?date_basis=edition").json()
        selected = client.get(f"/api/hacker-news?date_basis=edition&date={edition}").json()
        assert latest == selected
        assert selected["date"] == edition
        assert selected["dates"] == [edition]
        assert selected["content_date"] == content
        assert selected["items"][0]["object_id"] == "123"
        original = client.get(f"/api/hacker-news?date={content}").json()
        assert original["date"] == content
        assert original["dates"] == [content]
        assert original["items"] == selected["items"]
        missing = client.get("/api/hacker-news?date_basis=edition&date=2020-01-10").json()
        assert missing["date"] == "2020-01-10"
        assert missing["items"] == []
        assert client.get("/api/hacker-news?date_basis=invalid").status_code == 422
        if lag_days:
            assert (
                client.get("/api/hacker-news?date_basis=edition&date=0001-01-01").status_code == 422
            )
