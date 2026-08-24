"""领域对象。刻意保持成不可变的纯数据结构，方便在各层之间传递。"""

from dataclasses import dataclass

from .config import Period


@dataclass(frozen=True, slots=True)
class RawPage:
    """一次抓取的原始响应，未经解析。"""

    snapshot_date: str  # YYYY-MM-DD (CST)
    period: Period
    url: str
    http_status: int
    html: str
    fetched_at: str


@dataclass(frozen=True, slots=True)
class RepoSnapshot:
    """从榜单页面解析出的单个仓库条目，对应 snapshots 表一行。"""

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
