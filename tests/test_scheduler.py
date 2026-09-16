"""Verify the daily clock, failure isolation, and background execution."""

import asyncio
import threading
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from trend_sift import scheduler
from trend_sift.core.config import Settings


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-09-16T00:59:00+00:00", "2026-09-16T09:00:00+08:00"),
        ("2026-09-16T01:00:00+00:00", "2026-09-17T09:00:00+08:00"),
        ("2026-09-16T14:00:00+00:00", "2026-09-17T09:00:00+08:00"),
        ("2026-12-31T23:00:00+00:00", "2027-01-01T09:00:00+08:00"),
    ],
)
def test_next_beijing_run(now: str, expected: str) -> None:
    assert scheduler.next_run(
        datetime.fromisoformat(now), "09:00", "Asia/Shanghai"
    ).isoformat() == (expected)


def test_custom_time_and_timezone() -> None:
    target = scheduler.next_run(datetime(2026, 9, 16, 1, tzinfo=UTC), "17:30", "UTC")
    assert target == datetime(2026, 9, 16, 17, 30, tzinfo=UTC)


@pytest.mark.parametrize("value", ["25:00", "09:60", "9:00", "09:00:30"])
def test_invalid_schedule_time(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(schedule_time=value)


def test_schedule_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEDULE_ENABLED", "true")
    monkeypatch.setenv("SCHEDULE_TIME", "12:30")
    monkeypatch.setenv("SCHEDULE_TIMEZONE", "UTC")
    config = Settings()
    assert config.schedule_enabled
    assert config.schedule_time == "12:30"
    assert config.schedule_timezone == "UTC"
    with pytest.raises(ValidationError):
        Settings(schedule_timezone="Invalid/Timezone")


def test_failed_jobs_do_not_stop_future_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    sleep = AsyncMock(side_effect=[None, None, None, asyncio.CancelledError()])
    run = Mock(side_effect=[1, RuntimeError("offline"), 0])
    monkeypatch.setattr(scheduler.asyncio, "sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scheduler.daily_loop("09:00", "Asia/Shanghai", run))
    assert run.call_count == 3
    assert all(call.args[0] > 0 for call in sleep.call_args_list)


def test_pipeline_does_not_block_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler.asyncio, "sleep", AsyncMock())
    release = threading.Event()

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        started = asyncio.Event()

        def run() -> int:
            loop.call_soon_threadsafe(started.set)
            assert release.wait(timeout=3)
            return 0

        task = asyncio.create_task(scheduler.daily_loop("09:00", "Asia/Shanghai", run))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            # We can run on the API event loop while the synchronous job is still blocked.
            assert not task.done()
        finally:
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(exercise())


def test_pipeline_reuses_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    from trend_sift.cli import commands

    run = Mock(return_value=0)
    monkeypatch.setattr(commands, "cmd_run", run)
    assert scheduler.run_pipeline() == 0
    args = run.call_args.args[0]
    assert not args.dry_run
    assert args.github_date is None and args.hn_date is None
