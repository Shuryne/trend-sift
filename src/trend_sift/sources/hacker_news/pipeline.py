"""Hacker News 流水线：抓取 -> 归档 -> 解析 -> 富化 -> 摘要 -> 推送。

结构与 github/pipeline.py 对齐（同样以 SQLite 状态为输入输出、同样可分段
重跑），但两条流水线是**平行的两份代码**，不共享基类。理由见 README
「为什么不抽象数据源」：目前只有两个源，抽出来的公共接口一定是照着先写的
那个的形状长的，另一个只能扭曲着适配。等有第三个源时共性才会真正浮现。
"""

import logging
from typing import Any

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
    """抓取某日榜单并入库。返回本次运行的统计。"""
    day = snapshot_date or target_date()
    init_db()

    stats: dict[str, Any] = {"snapshot_date": day, "fetched": 0, "inserted": 0, "new_stories": 0}
    error: str | None = None

    with connect() as conn:
        run_id = start_run(conn, "hn")
        # 必须在写入今日快照前取，否则今天的帖子会被当成「以前见过」
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
        except Exception as exc:  # noqa: BLE001 — 由调用方决定告警还是继续
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
    """完整流水线。任一阶段失败都记录并继续，最后按需告警。"""
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
            # fetch 阶段自己吞了异常并记在 errors 里，这里要捞出来
            errors.extend(result.get("errors", []) if isinstance(result, dict) else [])
        except Exception as exc:  # noqa: BLE001 — 单阶段失败不应中断整条流水线
            log.exception("HN 阶段 %s 失败", name)
            stats[name] = {"error": str(exc)}
            errors.append(f"{name}: {exc}")

    stats["status"] = "ok" if not errors else "partial"
    stats["errors"] = errors

    # 静默失败是最糟的情况：任务挂了几周都没人发现。必须主动告警。
    if errors and alert_on_error and not dry_run:
        send_alert(
            f"Hacker News 任务异常（{day}）",
            "\n".join(f"- **{e}**" for e in errors),
        )

    return stats


def run_reparse(snapshot_date: str) -> dict[str, Any]:
    """从归档的原始 JSON 重新解析入库，不发起任何网络请求。"""
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
