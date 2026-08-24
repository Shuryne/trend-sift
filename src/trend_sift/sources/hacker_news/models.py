"""Hacker News domain objects kept separate from GitHub-specific models."""

from dataclasses import dataclass


def hn_url(object_id: str) -> str:
    """Return the stable Hacker News discussion URL for an item."""
    return f"https://news.ycombinator.com/item?id={object_id}"


def target_url(url: str | None, object_id: str) -> str:
    """Prefer an external article and otherwise use the discussion page."""
    return url or hn_url(object_id)


@dataclass(frozen=True, slots=True)
class RawFeed:
    """Unparsed JSON response from one Algolia query."""

    snapshot_date: str  # Source date in YYYY-MM-DD UTC form.
    url: str
    http_status: int
    body: str
    fetched_at: str


@dataclass(frozen=True, slots=True)
class StorySnapshot:
    """One ranked story persisted in the snapshot table."""

    snapshot_date: str
    rank: int
    object_id: str  # Algolia represents Hacker News item IDs as strings.
    title: str
    url: str | None  # Text posts have no external URL.
    story_text: str | None  # API-provided body for text posts.
    author: str
    points: int
    num_comments: int
    created_at_i: int  # Story creation time as a Unix timestamp.
    fetched_at: str

    @property
    def hn_url(self) -> str:
        return hn_url(self.object_id)

    @property
    def target_url(self) -> str:
        return target_url(self.url, self.object_id)
