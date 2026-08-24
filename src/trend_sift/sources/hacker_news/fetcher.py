"""Fetch a UTC day's high-scoring stories from the Algolia HN Search API.

The JSON API provides scores and comment counts needed for deterministic local ranking.
Calendar-day UTC windows make repeated runs address the same source interval.
"""

import datetime as dt
import json
import logging
from typing import Any
from urllib.parse import urlencode

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...core.config import settings
from ...core.store import now_iso
from .models import RawFeed, StorySnapshot

log = logging.getLogger(__name__)

API_URL = "https://hn.algolia.com/api/v1/search"
# Fetch extra candidates before applying deterministic local ranking.
FETCH_SIZE = 100

RETRYABLE = (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)


class FetchError(Exception):
    pass


def target_date(lag_days: int | None = None) -> str:
    """Return the target UTC date, defaulting to today minus the configured lag.

    Following the source timezone keeps reruns stable across local timezones.
    """
    lag = settings.hn_lag_days if lag_days is None else lag_days
    return (dt.datetime.now(dt.UTC) - dt.timedelta(days=lag)).strftime("%Y-%m-%d")


def _day_bounds(day: str) -> tuple[int, int]:
    """Convert a date into a half-open UTC Unix timestamp interval."""
    start = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.UTC)
    return int(start.timestamp()), int((start + dt.timedelta(days=1)).timestamp())


def build_url(day: str, min_points: int | None = None) -> str:
    lo, hi = _day_bounds(day)
    pts = settings.hn_min_points if min_points is None else min_points
    query = urlencode(
        {
            "tags": "story",
            "numericFilters": f"created_at_i>={lo},created_at_i<{hi},points>={pts}",
            "hitsPerPage": FETCH_SIZE,
        }
    )
    return f"{API_URL}?{query}"


@retry(
    retry=retry_if_exception_type(RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
def _get(client: httpx.Client, url: str) -> httpx.Response:
    resp = client.get(url)
    # Retry server failures and timeouts; client errors are not transient.
    if resp.status_code >= 500:
        resp.raise_for_status()
    return resp


def fetch_day(day: str, min_points: int | None = None) -> RawFeed:
    """Fetch raw JSON for a date, leaving parsing to the replayable parser."""
    url = build_url(day, min_points)
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = _get(client, url)
    if resp.status_code != 200:
        raise FetchError(f"Algolia 返回 HTTP {resp.status_code}")

    log.info("已抓取 HN %s：%d 字节", day, len(resp.content))
    return RawFeed(
        snapshot_date=day,
        url=url,
        http_status=resp.status_code,
        body=resp.text,
        fetched_at=now_iso(),
    )


class ParseError(Exception):
    """Signal that a response no longer matches the expected API structure."""


def _story(hit: dict[str, Any], day: str, rank: int, fetched_at: str) -> StorySnapshot | None:
    object_id = hit.get("objectID")
    title = (hit.get("title") or "").strip()
    # Entries without an ID or title are not usable stories.
    if not object_id or not title:
        return None
    return StorySnapshot(
        snapshot_date=day,
        rank=rank,
        object_id=str(object_id),
        title=title,
        # Text posts have no external URL and fall back to their discussion page.
        url=hit.get("url") or None,
        story_text=hit.get("story_text") or None,
        author=hit.get("author") or "",
        points=int(hit.get("points") or 0),
        num_comments=int(hit.get("num_comments") or 0),
        created_at_i=int(hit.get("created_at_i") or 0),
        fetched_at=fetched_at,
    )


def parse_feed(
    body: str, day: str, fetched_at: str, top_n: int | None = None
) -> list[StorySnapshot]:
    """Parse and return the top stories ordered by score.

    Local sorting avoids relying on Algolia's undocumented custom ranking.

    Raises:
        ParseError: The response is invalid JSON or does not contain a hits array.
    """
    n = settings.hn_top_n if top_n is None else top_n
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ParseError(f"HN {day} 响应不是合法 JSON：{exc}") from exc

    hits = data.get("hits")
    if not isinstance(hits, list):
        raise ParseError(f"HN {day} 响应中没有 hits 数组，可能是 API 变更或错误页")

    ranked = sorted(hits, key=lambda h: h.get("points") or 0, reverse=True)[:n]

    out: list[StorySnapshot] = []
    rank = 0
    for hit in ranked:
        rank += 1
        s = _story(hit, day, rank, fetched_at)
        if s is None:
            rank -= 1  # Invalid entries do not consume a rank.
            continue
        out.append(s)

    if hits and not out:
        raise ParseError(f"HN {day} 拿到 {len(hits)} 条但全部解析失败")
    return out
