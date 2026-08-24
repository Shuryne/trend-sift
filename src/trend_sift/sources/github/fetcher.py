"""Fetch GitHub Trending pages for the configured periods."""

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
# Use a browser-like user agent to avoid generic-client throttling policies.
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
    # Retry server failures and timeouts; client errors are not transient.
    if resp.status_code >= 500:
        resp.raise_for_status()
    return resp


def fetch_period(client: httpx.Client, period: Period, snapshot_date: str) -> RawPage:
    """Fetch one period without restricting the spoken language."""
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
    """Fetch every requested period while isolating per-period failures.

    Values are either raw pages or exceptions so the caller can report partial runs.
    """
    results: dict[Period, RawPage | Exception] = {}
    with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
        for i, period in enumerate(periods):
            if i > 0 and settings.request_delay > 0:
                time.sleep(settings.request_delay)
            try:
                results[period] = fetch_period(client, period, snapshot_date)
            except Exception as exc:  # noqa: BLE001 — isolate each period
                log.error("抓取 %s 榜单失败：%s", period, exc)
                results[period] = exc
    return results
