"""从 Algolia 的 HN Search API 取某一天的高分帖。

为什么不爬 news.ycombinator.com/front?day=，也不爬 daemonology 的 HN Daily：

  - 官方 HTML 页面要解析，且会 429；API 是 JSON，免费无鉴权，稳定得多。
  - daemonology 每条只有标题和链接，**没有分数和评论数** —— 拿不到数值
    就没法排序、设阈值、算增速，等于把选品权完全交给别人。

窗口的选择见 README「HN 的取数窗口」：抓 D-lag 那一整个 UTC 日，
而不是「现在往前 24 小时」。HN 的日期边界本来就是 UTC，跟着它走，
重跑同一天才能得到同一批数据。
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
# 一次多取一些再本地排序截断。原因见 _parse 的注释。
FETCH_SIZE = 100

RETRYABLE = (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)


class FetchError(Exception):
    pass


def target_date(lag_days: int | None = None) -> str:
    """要抓哪一天（UTC）。默认today - hn_lag_days。

    用 UTC 而不是 CST：HN 的「一天」是 UTC 划分的，官方 /front?day= 也按
    UTC 标注。跟着数据源的时区走，跨时区重跑才不会错位。
    """
    lag = settings.hn_lag_days if lag_days is None else lag_days
    return (dt.datetime.now(dt.UTC) - dt.timedelta(days=lag)).strftime("%Y-%m-%d")


def _day_bounds(day: str) -> tuple[int, int]:
    """把 YYYY-MM-DD 转成 [当日 00:00 UTC, 次日 00:00 UTC) 的时间戳。"""
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
    # 4xx 不重试（重试也没用），5xx 和超时才重试
    if resp.status_code >= 500:
        resp.raise_for_status()
    return resp


def fetch_day(day: str, min_points: int | None = None) -> RawFeed:
    """抓某一天的原始 JSON。解析交给 parse_feed，方便从归档重放。"""
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
    """响应结构变化导致无法解析时抛出，由上层决定是告警还是降级。"""


def _story(hit: dict[str, Any], day: str, rank: int, fetched_at: str) -> StorySnapshot | None:
    object_id = hit.get("objectID")
    title = (hit.get("title") or "").strip()
    # 没有 id 或标题的条目不是 story（可能是 comment 混进来了），跳过而不是塞脏数据
    if not object_id or not title:
        return None
    return StorySnapshot(
        snapshot_date=day,
        rank=rank,
        object_id=str(object_id),
        title=title,
        # 纯文本帖（Ask HN、自述帖）没有 url 字段，保持 None，
        # 由 models.target_url 兜底到讨论页
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
    """解析响应，按分数降序取前 N。

    **本地重排而不是信任 API 的返回顺序**：空查询下 Algolia 走的是索引的
    custom ranking，实测确实是分数降序，但这是未文档化的行为。多取 100 条
    再自己排，成本可以忽略，换来的是排序规则完全掌握在自己手里。

    Raises:
        ParseError: 响应里没有 hits 字段 —— 几乎可以肯定是 API 变更或
            返回了错误页，此时应告警而不是当成「今天没数据」。
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
            rank -= 1  # 跳过的条目不占用排名
            continue
        out.append(s)

    if hits and not out:
        raise ParseError(f"HN {day} 拿到 {len(hits)} 条但全部解析失败")
    return out
