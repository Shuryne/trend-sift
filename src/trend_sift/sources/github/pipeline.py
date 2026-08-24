"""GitHub pipeline: fetch, archive, parse, enrich, summarize, and notify.

SQLite-backed stages can be rerun independently. Historical snapshots survive fetch
failures, and notifications remain pending until their delivery records are written.
"""

import logging
import time
from typing import Any

from ...core.config import settings
from ...core.schema import init_db
from ...core.store import connect, finish_run, start_run, today_str
from ...notifications.feishu import send_alert
from .config import Period
from .enrich import enrich_repos
from .fetcher import fetch_all
from .models import RawPage
from .notify import items_by_period, mark_notified, send_digest
from .parser import parse_trending
from .store import (
    distinct_repos_on,
    load_raw_page,
    repos_seen_before,
    save_raw_page,
    save_snapshots,
)
from .summarize import PROMPT_VERSION, summarize_batch

log = logging.getLogger(__name__)


def run_fetch(periods: list[Period], snapshot_date: str | None = None) -> dict[str, Any]:
    """Fetch and persist a snapshot while returning run statistics."""
    date = snapshot_date or today_str()
    init_db()

    stats: dict[str, Any] = {
        "snapshot_date": date,
        "periods": {},
        "fetched": 0,
        "inserted": 0,
        "new_repos": 0,
    }
    errors: list[str] = []

    with connect() as conn:
        run_id = start_run(conn, "github")
        # Read prior repositories before today's rows affect novelty detection.
        seen_before = repos_seen_before(conn, date)
        today_repos: set[str] = set()

        results = fetch_all(periods, date)

        for period, result in results.items():
            if isinstance(result, Exception):
                errors.append(f"{period}: {result}")
                stats["periods"][period] = {"error": str(result)}
                continue

            page: RawPage = result
            save_raw_page(conn, page)

            try:
                snaps = parse_trending(page.html, date, period, page.fetched_at)
            except Exception as exc:  # noqa: BLE001 — isolate each period
                log.error("解析 %s 榜单失败：%s", period, exc)
                errors.append(f"{period} 解析失败: {exc}")
                stats["periods"][period] = {"error": f"parse: {exc}"}
                continue

            inserted = save_snapshots(conn, snaps)
            today_repos.update(s.full_name for s in snaps)

            stats["fetched"] += len(snaps)
            stats["inserted"] += inserted
            stats["periods"][period] = {"parsed": len(snaps), "inserted": inserted}
            log.info("%s 榜单：解析 %d 条，新增 %d 条", period, len(snaps), inserted)

        stats["new_repos"] = len(today_repos - seen_before)

        status = "ok" if not errors else ("failed" if len(errors) == len(periods) else "partial")
        finish_run(conn, run_id, status, stats, "; ".join(errors) or None)
        stats["status"] = status
        stats["errors"] = errors

    return stats


def run_enrich(snapshot_date: str | None = None, force: bool = False) -> dict[str, int]:
    date = snapshot_date or today_str()
    init_db()
    with connect() as conn:
        repos = [r["full_name"] for r in distinct_repos_on(conn, date)]
    if not repos:
        log.warning("%s 没有快照数据，先跑 fetch", date)
        return {"enriched": 0, "cached": 0, "failed": 0, "skipped_rate_limit": 0}
    return enrich_repos(repos, force=force)


def run_summarize(snapshot_date: str | None = None, force: bool = False) -> dict[str, Any]:
    started = time.perf_counter()
    date = snapshot_date or today_str()
    init_db()
    with connect() as conn:
        repos = [(r["full_name"], r["description"]) for r in distinct_repos_on(conn, date)]
    if not repos:
        log.warning("%s 没有快照数据，先跑 fetch", date)
        results = []
    else:
        results = summarize_batch(repos, force=force)

    ok = sum(1 for r in results if r.summary_zh)
    return {
        "attempted": len(results),
        "ok": ok,
        "failed": len(results) - ok,
        "skipped_cached": len(repos) - len(results),
        "prompt_version": PROMPT_VERSION,
        "concurrency": settings.llm_summary_concurrency,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def run_notify(snapshot_date: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    date = snapshot_date or today_str()
    init_db()
    grouped = items_by_period(date, PROMPT_VERSION)
    counts = {p: len(v) for p, v in grouped.items()}

    if dry_run:
        log.info("dry-run：%s，未实际发送", counts or "无待推送项目")
        return {"by_period": counts, "cards": 0, "dry_run": True}

    if not grouped:
        return {"by_period": {}, "cards": 0}

    cards = send_digest(grouped, date)
    mark_notified(grouped, date)
    return {"by_period": counts, "cards": cards}


def run_all(
    periods: list[Period],
    snapshot_date: str | None = None,
    dry_run: bool = False,
    alert_on_error: bool = True,
) -> dict[str, Any]:
    """Run every stage, isolate failures, and send an alert when appropriate."""
    date = snapshot_date or today_str()
    stats: dict[str, Any] = {"snapshot_date": date}
    errors: list[str] = []

    stages: list[tuple[str, Any]] = [
        ("fetch", lambda: run_fetch(periods, date)),
        ("enrich", lambda: run_enrich(date)),
        ("summarize", lambda: run_summarize(date)),
        ("notify", lambda: run_notify(date, dry_run=dry_run)),
    ]

    for name, fn in stages:
        try:
            result = fn()
            stats[name] = result
            # Fetch isolates period failures, so propagate its error list here.
            errors.extend(result.get("errors", []) if isinstance(result, dict) else [])
        except Exception as exc:  # noqa: BLE001 — isolate pipeline stages
            log.exception("阶段 %s 失败", name)
            stats[name] = {"error": str(exc)}
            errors.append(f"{name}: {exc}")

    stats["status"] = "ok" if not errors else "partial"
    stats["errors"] = errors

    # Surface unattended failures instead of relying solely on cron logs.
    if errors and alert_on_error and not dry_run:
        send_alert(
            f"GitHub Trending 任务异常（{date}）",
            "\n".join(f"- **{e}**" for e in errors),
        )

    return stats


def run_reparse(snapshot_date: str, periods: list[Period]) -> dict[str, Any]:
    """Reparse archived HTML without issuing network requests.

    This supports parser repairs and extraction of newly introduced fields.
    """
    init_db()
    stats: dict[str, Any] = {"snapshot_date": snapshot_date, "periods": {}}

    with connect() as conn:
        for period in periods:
            html = load_raw_page(conn, snapshot_date, period)
            if html is None:
                stats["periods"][period] = {"error": "无归档 HTML"}
                log.warning("%s %s 没有归档的原始 HTML", snapshot_date, period)
                continue
            snaps = parse_trending(html, snapshot_date, period, f"{snapshot_date}T00:00:00+08:00")
            inserted = save_snapshots(conn, snaps)
            stats["periods"][period] = {"parsed": len(snaps), "inserted": inserted}
            log.info(
                "重放 %s %s：解析 %d 条，新增 %d 条",
                snapshot_date,
                period,
                len(snaps),
                inserted,
            )

    return stats
