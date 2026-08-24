"""Hacker News pipeline: fetch, archive, parse, enrich, summarize, and notify.

Stages use SQLite as their boundary and can be rerun independently. The pipeline remains
separate from GitHub because their source-specific fields and behavior differ materially.
"""

import logging
import time
from typing import Any

from ...core.config import settings
from ...core.schema import init_db
from ...core.store import connect, finish_run, start_run
from ...notifications.feishu import send_alert
from .enrich import enrich_stories
from .fetcher import fetch_day, parse_feed, target_date
from .notify import items_for, mark_notified, send_digest
from .store import (
    load_raw_feed,
    save_raw_feed,
    save_snapshots,
    stories_on,
    stories_seen_before,
)
from .summarize import PROMPT_VERSION, summarize_batch

log = logging.getLogger(__name__)


def run_fetch(snapshot_date: str | None = None) -> dict[str, Any]:
    """Fetch and persist one UTC day while returning run statistics."""
    day = snapshot_date or target_date()
    init_db()

    stats: dict[str, Any] = {"snapshot_date": day, "fetched": 0, "inserted": 0, "new_stories": 0}
    error: str | None = None

    with connect() as conn:
        run_id = start_run(conn, "hn")
        # Read prior stories before today's rows affect novelty detection.
        seen_before = stories_seen_before(conn, day)

        try:
            feed = fetch_day(day)
            save_raw_feed(conn, feed)
            stories = parse_feed(feed.body, day, feed.fetched_at)

            inserted = save_snapshots(conn, stories)
            stats["fetched"] = len(stories)
            stats["inserted"] = inserted
            stats["new_stories"] = len({s.object_id for s in stories} - seen_before)
            log.info("HN %s：解析 %d 条，新增 %d 条", day, len(stories), inserted)
        except Exception as exc:  # noqa: BLE001 — let callers decide alert behavior
            log.exception("HN %s 抓取失败", day)
            error = str(exc)

        status = "failed" if error else "ok"
        finish_run(conn, run_id, status, stats, error)
        stats["status"] = status
        stats["errors"] = [error] if error else []

    return stats


def run_enrich(snapshot_date: str | None = None, force: bool = False) -> dict[str, int]:
    day = snapshot_date or target_date()
    init_db()
    with connect() as conn:
        rows = stories_on(conn, day)
        stories = [(r["object_id"], r["url"], r["story_text"]) for r in rows]
    if not stories:
        log.warning("%s 没有 HN 快照数据，先跑 fetch", day)
        return {"enriched": 0, "cached": 0, "no_url": 0, "failed": 0}
    return enrich_stories(stories, force=force)


def run_summarize(snapshot_date: str | None = None, force: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    day = snapshot_date or target_date()
    init_db()
    with connect() as conn:
        stories = [(r["object_id"], r["title"]) for r in stories_on(conn, day)]
    if not stories:
        log.warning("%s 没有 HN 快照数据，先跑 fetch", day)
        results = []
    else:
        results = summarize_batch(stories, force=force)

    ok = sum(1 for r in results if r.summary_zh)
    return {
        "attempted": len(results),
        "ok": ok,
        "failed": len(results) - ok,
        "skipped_cached": len(stories) - len(results),
        "prompt_version": PROMPT_VERSION,
        "concurrency": settings.llm_summary_concurrency,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def run_notify(snapshot_date: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    day = snapshot_date or target_date()
    init_db()
    items = items_for(day, PROMPT_VERSION)

    if dry_run:
        log.info("dry-run：%d 条待推送，未实际发送", len(items))
        return {"items": len(items), "cards": 0, "dry_run": True}

    if not items:
        return {"items": 0, "cards": 0}

    cards = send_digest(items, day)
    mark_notified(items, day)
    return {"items": len(items), "cards": cards}


def run_all(
    snapshot_date: str | None = None,
    dry_run: bool = False,
    alert_on_error: bool = True,
) -> dict[str, Any]:
    """Run every stage, isolate failures, and send an alert when appropriate."""
    day = snapshot_date or target_date()
    stats: dict[str, Any] = {"snapshot_date": day}
    errors: list[str] = []

    stages: list[tuple[str, Any]] = [
        ("fetch", lambda: run_fetch(day)),
        ("enrich", lambda: run_enrich(day)),
        ("summarize", lambda: run_summarize(day)),
        ("notify", lambda: run_notify(day, dry_run=dry_run)),
    ]

    for name, fn in stages:
        try:
            result = fn()
            stats[name] = result
            # Fetch records its own failure, so propagate its error list here.
            errors.extend(result.get("errors", []) if isinstance(result, dict) else [])
        except Exception as exc:  # noqa: BLE001 — isolate pipeline stages
            log.exception("HN 阶段 %s 失败", name)
            stats[name] = {"error": str(exc)}
            errors.append(f"{name}: {exc}")

    stats["status"] = "ok" if not errors else "partial"
    stats["errors"] = errors

    # Surface unattended failures instead of relying solely on cron logs.
    if errors and alert_on_error and not dry_run:
        send_alert(
            f"Hacker News 任务异常（{day}）",
            "\n".join(f"- **{e}**" for e in errors),
        )

    return stats


def run_reparse(snapshot_date: str) -> dict[str, Any]:
    """Reparse archived JSON without issuing network requests."""
    init_db()
    with connect() as conn:
        body = load_raw_feed(conn, snapshot_date)
        if body is None:
            log.warning("%s 没有归档的 HN 原始响应", snapshot_date)
            return {"snapshot_date": snapshot_date, "error": "无归档 JSON"}
        stories = parse_feed(body, snapshot_date, f"{snapshot_date}T00:00:00+00:00")
        inserted = save_snapshots(conn, stories)
        log.info("重放 HN %s：解析 %d 条，新增 %d 条", snapshot_date, len(stories), inserted)
    return {"snapshot_date": snapshot_date, "parsed": len(stories), "inserted": inserted}
