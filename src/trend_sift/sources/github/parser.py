"""Parse GitHub Trending using structural selectors instead of utility classes.

Repository links, semantic attributes, and URL suffixes are more stable than GitHub's
frequently changing atomic CSS classes. ``article.Box-row`` remains the item boundary.
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .config import Period
from .models import RepoSnapshot

_INT_RE = re.compile(r"[\d,]+")
_STARS_PERIOD_RE = re.compile(r"([\d,]+)\s+stars?\s+(?:today|this week|this month)")


class ParseError(Exception):
    """Signal that the returned page no longer matches the expected structure."""


def _to_int(text: str | None) -> int | None:
    if not text:
        return None
    m = _INT_RE.search(text)
    return int(m.group().replace(",", "")) if m else None


def _text(node: LexborNode | None) -> str | None:
    if node is None:
        return None
    t = node.text(strip=True)
    return t or None


def _parse_article(
    art: LexborNode,
    snapshot_date: str,
    period: Period,
    rank: int,
    fetched_at: str,
) -> RepoSnapshot | None:
    heading = art.css_first("h2 a[href]")
    if heading is None:
        return None
    href = (heading.attributes.get("href") or "").strip()
    full_name = href.lstrip("/")
    # Valid repository links have exactly the /owner/repository shape.
    if full_name.count("/") != 1 or not all(full_name.split("/")):
        return None

    stars = forks = None
    for a in art.css("a[href]"):
        h = a.attributes.get("href") or ""
        if h.endswith("/stargazers"):
            stars = _to_int(a.text())
        elif h.endswith("/forks"):
            forks = _to_int(a.text())

    stars_period = None
    for span in art.css("span"):
        m = _STARS_PERIOD_RE.search(span.text())
        if m:
            stars_period = int(m.group(1).replace(",", ""))
            break

    return RepoSnapshot(
        snapshot_date=snapshot_date,
        period=period,
        rank=rank,
        full_name=full_name,
        description=_text(art.css_first("p")),
        language=_text(art.css_first('span[itemprop="programmingLanguage"]')),
        stars=stars,
        forks=forks,
        stars_period=stars_period,
        fetched_at=fetched_at,
    )


def parse_trending(
    html: str, snapshot_date: str, period: Period, fetched_at: str
) -> list[RepoSnapshot]:
    """Parse repositories in their original page order.

    Raises:
        ParseError: No valid item container was found in the response.
    """
    tree = LexborHTMLParser(html)
    articles = tree.css("article.Box-row")
    if not articles:
        raise ParseError(
            f"{period} 榜单未找到任何 article.Box-row，可能是 GitHub 改版、返回了错误页或触发了反爬"
        )

    out: list[RepoSnapshot] = []
    rank = 0
    for art in articles:
        rank += 1
        snap = _parse_article(art, snapshot_date, period, rank, fetched_at)
        if snap is None:
            rank -= 1  # Invalid entries do not consume a rank.
            continue
        out.append(snap)

    if not out:
        raise ParseError(f"{period} 榜单找到 {len(articles)} 个条目但全部解析失败")
    return out
