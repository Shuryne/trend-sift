"""Explicit public response contracts, separate from storage rows."""

from pydantic import BaseModel


class GithubItem(BaseModel):
    rank: int
    full_name: str
    is_new: bool
    days_on_board: int
    first_seen: str
    description: str | None
    language: str | None
    stars: int | None
    stars_period: int | None
    summary_zh: str | None


class HackerNewsItem(BaseModel):
    rank: int
    object_id: str
    title: str
    url: str | None
    points: int | None
    num_comments: int | None
    summary_zh: str | None


class GithubBoard(BaseModel):
    date: str | None
    dates: list[str]
    updated_at: str | None
    items: list[GithubItem]


class HackerNewsBoard(BaseModel):
    content_date: str | None
    date: str | None
    dates: list[str]
    updated_at: str | None
    items: list[HackerNewsItem]
