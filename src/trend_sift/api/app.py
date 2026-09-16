"""Serve archived boards and the compiled frontend with optional daily background scheduling."""

import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager, closing, suppress
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..core.config import PROJECT_ROOT, settings
from ..core.schema import init_db
from ..scheduler import daily_loop
from ..sources.github.config import Period
from .schemas import GithubBoard, GithubItem, HackerNewsBoard, HackerNewsItem


def create_app(
    db_path: Path | None = None,
    web_dir: Path | None = None,
    *,
    schedule_enabled: bool | None = None,
) -> FastAPI:
    """Create an independently testable app with a shared persistent database."""
    database = (db_path or settings.db_path).resolve()
    frontend = web_dir or Path(os.getenv("TREND_SIFT_WEB_DIR", str(PROJECT_ROOT / "web/dist")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(database)
        enabled = settings.schedule_enabled if schedule_enabled is None else schedule_enabled
        task = None
        if enabled:
            from ..cli.commands import setup_logging

            setup_logging()
            task = asyncio.create_task(
                daily_loop(settings.schedule_time, settings.schedule_timezone)
            )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="trend-sift", lifespan=lifespan)

    def read_connection() -> sqlite3.Connection:
        conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @app.get("/api/health")
    def health() -> dict[str, str]:
        with closing(read_connection()) as conn:
            conn.execute("SELECT 1 FROM gh_snapshots LIMIT 1")
        return {"status": "ok"}

    @app.get("/api/github", response_model=GithubBoard)
    def github(
        period: Period = "daily",
        snapshot_date: Annotated[date | None, Query(alias="date")] = None,
    ) -> GithubBoard:
        with closing(read_connection()) as conn:
            dates = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT snapshot_date FROM gh_snapshots WHERE period=? "
                    "ORDER BY snapshot_date DESC",
                    (period,),
                )
            ]
            selected = snapshot_date.isoformat() if snapshot_date else next(iter(dates), None)
            rows = conn.execute(
                """SELECT s.*, (SELECT summary_zh FROM gh_summaries m
                   WHERE m.full_name=s.full_name AND trim(m.summary_zh) != ''
                   ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS summary_zh,
                   NOT EXISTS (SELECT 1 FROM gh_snapshots h
                       WHERE h.full_name=s.full_name
                       AND h.snapshot_date < s.snapshot_date) AS is_new,
                   (SELECT COUNT(DISTINCT h.snapshot_date) FROM gh_snapshots h
                       WHERE h.full_name=s.full_name
                       AND h.snapshot_date <= s.snapshot_date) AS days_on_board,
                   (SELECT MIN(h.snapshot_date) FROM gh_snapshots h
                       WHERE h.full_name=s.full_name
                       AND h.snapshot_date <= s.snapshot_date) AS first_seen
                   FROM gh_snapshots s WHERE snapshot_date=? AND period=? ORDER BY rank""",
                (selected, period),
            ).fetchall()
        return GithubBoard(
            date=selected,
            dates=dates,
            updated_at=max((r["fetched_at"] for r in rows), default=None),
            items=[GithubItem.model_validate(dict(r)) for r in rows],
        )

    @app.get("/api/hacker-news", response_model=HackerNewsBoard)
    def hacker_news(
        snapshot_date: Annotated[date | None, Query(alias="date")] = None,
    ) -> HackerNewsBoard:
        with closing(read_connection()) as conn:
            dates = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT snapshot_date FROM hn_snapshots ORDER BY snapshot_date DESC"
                )
            ]
            selected = snapshot_date.isoformat() if snapshot_date else next(iter(dates), None)
            rows = conn.execute(
                """SELECT s.*, (SELECT summary_zh FROM hn_summaries m
                   WHERE m.object_id=s.object_id AND trim(m.summary_zh) != ''
                   ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS summary_zh
                   FROM hn_snapshots s WHERE snapshot_date=? ORDER BY rank""",
                (selected,),
            ).fetchall()
        return HackerNewsBoard(
            date=selected,
            dates=dates,
            updated_at=max((r["fetched_at"] for r in rows), default=None),
            items=[HackerNewsItem.model_validate(dict(r)) for r in rows],
        )

    if (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        if not (frontend / "index.html").is_file():
            raise HTTPException(503, "前端尚未构建，请先在 web 目录执行 npm ci && npm run build")
        return FileResponse(frontend / "index.html")

    return app


app = create_app()
