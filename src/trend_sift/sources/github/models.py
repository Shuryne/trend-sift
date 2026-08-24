"""Immutable domain objects passed between GitHub pipeline stages."""

from dataclasses import dataclass

from .config import Period


@dataclass(frozen=True, slots=True)
class RawPage:
    """Unparsed response captured from one trending-page request."""

    snapshot_date: str  # YYYY-MM-DD (CST)
    period: Period
    url: str
    http_status: int
    html: str
    fetched_at: str


@dataclass(frozen=True, slots=True)
class RepoSnapshot:
    """One repository entry parsed from a trending page."""

    snapshot_date: str
    period: Period
    rank: int
    full_name: str
    description: str | None
    language: str | None
    stars: int | None
    forks: int | None
    stars_period: int | None
    fetched_at: str
