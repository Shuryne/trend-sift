"""解析 GitHub Trending 页面。

定位策略刻意避开 class 名：GitHub 近期把 `mr-3` 批量改成了 `tmp-mr-3`，
说明它们的原子 class 随时会变。因此改用结构性更强的锚点：
  - 仓库名   -> h2 下第一个 <a> 的 href
  - 星标/fork -> href 以 /stargazers、/forks 结尾的 <a>
  - 语言     -> span[itemprop="programmingLanguage"]（语义属性，最稳定）
  - 增量星数 -> 文本中含 "stars today/this week/this month" 的 span
唯一保留的 class 依赖是 article.Box-row，它是条目的容器，短期内不会动。
"""

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from .config import Period
from .models import RepoSnapshot

_INT_RE = re.compile(r"[\d,]+")
_STARS_PERIOD_RE = re.compile(r"([\d,]+)\s+stars?\s+(?:today|this week|this month)")


class ParseError(Exception):
    """页面结构变化导致无法解析时抛出，由上层决定是告警还是降级。"""


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
    # href 形如 /owner/repo，其他形状都说明结构变了，跳过而不是硬塞脏数据
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
    """解析榜单 HTML，返回按页面顺序排名的仓库列表。

    Raises:
        ParseError: 页面里一个 article.Box-row 都找不到 —— 几乎可以肯定是
            GitHub 改版或返回了错误页，此时应告警而不是当成「今天没数据」。
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
            rank -= 1  # 跳过的条目不占用排名
            continue
        out.append(snap)

    if not out:
        raise ParseError(f"{period} 榜单找到 {len(articles)} 个条目但全部解析失败")
    return out
