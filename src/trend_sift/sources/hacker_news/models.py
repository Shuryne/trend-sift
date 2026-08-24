"""Hacker News 的领域对象。

刻意不和 GitHub 的 RepoSnapshot 共用基类：两边字段几乎没有交集
（stars/forks/language vs points/num_comments/author），强行抽出一个公共
父类只会得到一堆恒为 None 的字段。理由见 README「为什么不抽象数据源」。
"""

from dataclasses import dataclass


def hn_url(object_id: str) -> str:
    """HN 讨论页。永远存在，是外链缺失时的兜底。"""
    return f"https://news.ycombinator.com/item?id={object_id}"


def target_url(url: str | None, object_id: str) -> str:
    """点标题该去哪 —— 有外链去外链，纯文本帖去讨论页。"""
    return url or hn_url(object_id)


@dataclass(frozen=True, slots=True)
class RawFeed:
    """一次 Algolia 查询的原始 JSON 响应，未经解析。"""

    snapshot_date: str  # 该批数据所属的 HN 日期 (YYYY-MM-DD, UTC)
    url: str
    http_status: int
    body: str
    fetched_at: str


@dataclass(frozen=True, slots=True)
class StorySnapshot:
    """榜单上的单条 story，对应 hn_snapshots 表一行。"""

    snapshot_date: str
    rank: int
    object_id: str  # HN item id，字符串形式（Algolia 就是这么返回的）
    title: str
    url: str | None  # 纯文本帖（Ask HN / 自述帖）没有外链，这里为 None
    story_text: str | None  # 纯文本帖的正文，API 直接给，外链帖为 None
    author: str
    points: int
    num_comments: int
    created_at_i: int  # 发帖时间的 Unix 时间戳
    fetched_at: str

    @property
    def hn_url(self) -> str:
        return hn_url(self.object_id)

    @property
    def target_url(self) -> str:
        return target_url(self.url, self.object_id)
