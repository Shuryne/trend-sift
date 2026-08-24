"""GitHub-specific validation for daily, weekly, and monthly periods."""

from typing import Literal, get_args

from ...core.config import settings

Period = Literal["daily", "weekly", "monthly"]
PERIODS: tuple[Period, ...] = get_args(Period)


def period_list() -> list[Period]:
    """Parse and validate ``GITHUB_PERIODS`` without silently dropping values."""
    out: list[Period] = []
    for raw in settings.periods.split(","):
        p = raw.strip()
        if p in PERIODS:
            out.append(p)  # type: ignore[arg-type]
        elif p:
            raise ValueError(f"GITHUB_PERIODS 含有无效值 {p!r}，只允许 {'/'.join(PERIODS)}")
    if not out:
        raise ValueError("GITHUB_PERIODS 不能为空")
    return out
