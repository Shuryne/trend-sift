"""A single in-process daily task for the single-worker web service."""

import argparse
import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def next_run(now: datetime, schedule_time: str, timezone: str) -> datetime:
    """Return the next future wall-clock occurrence in the configured timezone."""
    local = now.astimezone(ZoneInfo(timezone))
    hour, minute = map(int, schedule_time.split(":"))
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target


def run_pipeline() -> int:
    """Reuse the manual pipeline, including its existing logging and notifications."""
    from .cli.commands import cmd_run

    return cmd_run(argparse.Namespace(github_date=None, hn_date=None, dry_run=False))


async def daily_loop(
    schedule_time: str, timezone: str, run: Callable[[], int] = run_pipeline
) -> None:
    """Wait for the next daily time; serialize jobs and keep the API responsive."""
    while True:
        now = datetime.now(ZoneInfo(timezone))
        target = next_run(now, schedule_time, timezone)
        log.info("下次定时任务：%s", target.isoformat())
        await asyncio.sleep(target.timestamp() - now.timestamp())
        try:
            result = await asyncio.to_thread(run)
            if result:
                log.error("定时任务执行失败，退出码 %s；下次按计划运行", result)
            else:
                log.info("定时任务完成")
        except Exception:  # noqa: BLE001 — one failed run must not terminate future scheduling
            log.exception("定时任务异常；下次按计划运行")
