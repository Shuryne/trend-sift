"""Fetch article text to enrich Hacker News summaries.

External sites may use paywalls, client-side rendering, anti-bot controls, or unusual
encodings. Failures therefore degrade to title-only summaries. Text posts use the API
body directly and require no external request.
"""

import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from ...core.config import CST
from ...core.store import connect, now_iso

log = logging.getLogger(__name__)

# Store more article text than the current prompt consumes.
ARTICLE_MAX_CHARS = 2500
# Published article bodies rarely change, so cache successful extraction for 30 days.
ENRICH_TTL_DAYS = 30
# Failed third-party pages get a short negative cache so transient failures recover tomorrow.
FAILED_ENRICH_TTL_DAYS = 1
FETCH_TIMEOUT = 10.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Prefer semantic containers, then common content classes, then the full body.
_CONTENT_SELECTORS = ("article", "main", '[role="main"]', ".post-content", ".entry-content")
_DROP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript", "form")
_WS_RE = re.compile(r"\s+")
_HTML_ENTITY_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class StoryMeta:
    object_id: str
    story_text: str | None
    article_head: str | None
    site: str | None


@dataclass(slots=True)
class ArticleFetch:
    text: str | None
    outcome: str


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _strip_html(raw: str) -> str:
    """Convert Algolia's escaped ``story_text`` HTML into plain text."""
    unescaped = (
        raw.replace("&#x2F;", "/")
        .replace("&#x27;", "'")
        .replace("&quot;", '"')
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("<p>", "\n")
    )
    return _clean(_HTML_ENTITY_RE.sub(" ", unescaped))


def extract_article(html: str) -> str | None:
    """Extract useful article text from arbitrary HTML."""
    tree = LexborHTMLParser(html)
    for tag in _DROP_TAGS:
        for node in tree.css(tag):
            node.decompose()

    for sel in _CONTENT_SELECTORS:
        node = tree.css_first(sel)
        if node:
            text = _clean(node.text())
            if len(text) > 200:  # Short matches are likely navigation or metadata.
                return text[:ARTICLE_MAX_CHARS]

    body = tree.css_first("body")
    text = _clean(body.text()) if body else ""
    return text[:ARTICLE_MAX_CHARS] or None


@retry(
    retry=retry_if_exception_type(
        (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)
    ),
    stop=stop_after_attempt(2),
    wait=wait_fixed(1),
    reraise=True,
)
def _get_article(client: httpx.Client, url: str) -> httpx.Response:
    """Fetch once, retrying only transient network errors and 5xx responses."""
    response = client.get(url)
    if response.status_code >= 500:
        response.raise_for_status()
    return response


def fetch_article(client: httpx.Client, url: str) -> ArticleFetch:
    """Fetch and extract an article while retaining a low-cardinality outcome."""
    try:
        resp = _get_article(client, url)
    except httpx.TimeoutException as exc:
        log.debug("%s 抓取失败：%s", url, exc)
        return ArticleFetch(None, "timeout")
    except httpx.NetworkError as exc:
        log.debug("%s 抓取失败：%s", url, exc)
        return ArticleFetch(None, "network_error")
    except httpx.HTTPStatusError as exc:
        log.debug("%s 重试后仍返回 HTTP %d", url, exc.response.status_code)
        return ArticleFetch(None, "http_error")
    except Exception as exc:  # noqa: BLE001 — third-party clients can fail unpredictably
        log.debug("%s 抓取失败：%s", url, exc)
        return ArticleFetch(None, "network_error")

    if resp.status_code != 200:
        log.debug("%s 返回 HTTP %d", url, resp.status_code)
        return ArticleFetch(None, "http_error")
    if "html" not in resp.headers.get("content-type", "").lower():
        log.debug("%s 不是 HTML（%s），跳过", url, resp.headers.get("content-type"))
        return ArticleFetch(None, "non_html")

    try:
        text = extract_article(resp.text)
    except Exception as exc:  # noqa: BLE001 — tolerate arbitrary page structures
        log.debug("%s 正文提取失败：%s", url, exc)
        return ArticleFetch(None, "extract_empty")
    return ArticleFetch(text, "ok" if text else "extract_empty")


def _needs_enrich(conn: sqlite3.Connection, object_id: str, has_external_url: bool) -> bool:
    row = conn.execute(
        "SELECT article_head, enriched_at FROM hn_stories WHERE object_id = ?", (object_id,)
    ).fetchone()
    if row is None:
        return True
    try:
        age = datetime.now(CST) - datetime.fromisoformat(row["enriched_at"])
    except ValueError:
        return True
    ttl_days = (
        FAILED_ENRICH_TTL_DAYS if has_external_url and not row["article_head"] else ENRICH_TTL_DAYS
    )
    return age > timedelta(days=ttl_days)


def _save(conn: sqlite3.Connection, meta: StoryMeta) -> None:
    conn.execute(
        """
        INSERT INTO hn_stories (object_id, story_text, article_head, site, enriched_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(object_id) DO UPDATE SET
            story_text=excluded.story_text,
            article_head=excluded.article_head,
            site=excluded.site,
            enriched_at=excluded.enriched_at
        """,
        (meta.object_id, meta.story_text, meta.article_head, meta.site, now_iso()),
    )


def enrich_stories(
    stories: list[tuple[str, str | None, str | None]], force: bool = False
) -> dict[str, int]:
    """Enrich ``(object_id, url, story_text_raw)`` tuples.

    The ``failed`` count means an external article was unavailable, which is expected.
    """
    stats = {
        "enriched": 0,
        "cached": 0,
        "no_url": 0,
        "failed": 0,
        "timeout": 0,
        "network_error": 0,
        "http_error": 0,
        "non_html": 0,
        "extract_empty": 0,
    }

    with connect() as conn:
        todo = (
            list(stories)
            if force
            else [s for s in stories if _needs_enrich(conn, s[0], bool(s[1]))]
        )
        stats["cached"] = len(stories) - len(todo)
        if not todo:
            log.info("全部 %d 条 story 均有新鲜缓存，跳过富化", len(stories))
            return stats

        with httpx.Client(headers=HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
            for object_id, url, story_text_raw in todo:
                story_text = _strip_html(story_text_raw) if story_text_raw else None

                if not url:
                    # Text posts already contain their body in the API response.
                    _save(conn, StoryMeta(object_id, story_text, None, None))
                    stats["no_url"] += 1
                    continue

                result = fetch_article(client, url)
                site = urlparse(url).netloc or None
                _save(conn, StoryMeta(object_id, story_text, result.text, site))
                if result.text:
                    stats["enriched"] += 1
                else:
                    stats["failed"] += 1
                    stats[result.outcome] += 1

    log.info(
        "HN 富化完成：抓到正文 %d，纯文本帖 %d，命中缓存 %d，未抓到正文 %d",
        stats["enriched"],
        stats["no_url"],
        stats["cached"],
        stats["failed"],
    )
    if stats["failed"]:
        reasons = ", ".join(
            f"{name}={stats[name]}"
            for name in ("timeout", "network_error", "http_error", "non_html", "extract_empty")
            if stats[name]
        )
        log.info("HN 正文不可用原因：%s；失败结果缓存 %d 天", reasons, FAILED_ENRICH_TTL_DAYS)
    return stats
