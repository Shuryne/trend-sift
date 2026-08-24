"""抓取 GitHub Trending 页面。

榜单每天只刷新一次，且没有可用的 Last-Modified / 强 ETag，因此抓取频率
保持每天 1-2 次即可 —— 时间点的选择见 README「关于 9 点这个时间」。
"""

import logging
import time

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...core.config import settings
from ...core.store import now_iso
from .config import Period
from .models import RawPage

log = logging.getLogger(__name__)

BASE_URL = "https://github.com/trending"
# 带上常见浏览器 UA。GitHub 对匿名抓取没有反爬，但默认的 python-httpx UA
# 更容易在未来被限流策略挑出来。
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

RETRYABLE = (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError)


class FetchError(Exception):
    pass


@retry(
    retry=retry_if_exception_type(RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
def _get(client: httpx.Client, url: str, params: dict[str, str]) -> httpx.Response:
    resp = client.get(url, params=params)
    # 4xx 不重试（重试也没用），5xx 和超时才重试
    if resp.status_code >= 500:
        resp.raise_for_status()
    return resp


def fetch_period(client: httpx.Client, period: Period, snapshot_date: str) -> RawPage:
    """抓取单个榜单。spoken_language_code 留空表示不限语言。"""
    params = {"since": period, "spoken_language_code": ""}
    resp = _get(client, BASE_URL, params)
    if resp.status_code != 200:
        raise FetchError(f"{period} 榜单返回 HTTP {resp.status_code}")

    log.info("已抓取 %s 榜单：%d 字节", period, len(resp.content))
    return RawPage(
        snapshot_date=snapshot_date,
        period=period,
        url=str(resp.url),
        http_status=resp.status_code,
        html=resp.text,
        fetched_at=now_iso(),
    )


def fetch_all(periods: list[Period], snapshot_date: str) -> dict[Period, RawPage | Exception]:
    """抓取所有榜单。

    单个榜单失败不影响其余榜单 —— 返回值里既可能是 RawPage 也可能是异常，
    由调用方决定这次运行算 ok 还是 partial。
    """
    results: dict[Period, RawPage | Exception] = {}
    with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
        for i, period in enumerate(periods):
            if i > 0 and settings.request_delay > 0:
                time.sleep(settings.request_delay)
            try:
                results[period] = fetch_period(client, period, snapshot_date)
            except Exception as exc:  # noqa: BLE001 — 逐榜隔离失败
                log.error("抓取 %s 榜单失败：%s", period, exc)
                results[period] = exc
    return results
