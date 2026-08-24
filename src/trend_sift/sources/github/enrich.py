"""Enrich trending repositories through the official GitHub REST API.

Topics and README excerpts provide better summary context. Enrichment stops before
exhausting the API quota and falls back to the description already on the board.
"""

import base64
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ...core.config import CST, settings
from ...core.store import connect, now_iso

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
# Keep more README text than the current prompt needs to allow prompt revisions.
README_MAX_CHARS = 2000
# Repository metadata changes slowly, so a weekly refresh is sufficient.
ENRICH_TTL_DAYS = 7


@dataclass(slots=True)
class RepoMeta:
    full_name: str
    owner: str
    topics: list[str]
    readme_head: str | None
    homepage: str | None
    license: str | None
    created_at: str | None


class RateLimited(Exception):
    """Signal that callers should stop enrichment and use board descriptions."""


def _headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "trend-sift",
    }
    if settings.github_token:
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=1, max=10),
    reraise=True,
)
def _get(client: httpx.Client, path: str, **kwargs) -> httpx.Response:
    resp = client.get(f"{API_BASE}{path}", **kwargs)
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if resp.status_code == 403 and remaining == "0":
        reset = resp.headers.get("X-RateLimit-Reset", "?")
        raise RateLimited(f"GitHub API 额度耗尽，重置时间戳 {reset}")
    return resp


def _fetch_readme(client: httpx.Client, full_name: str) -> str | None:
    try:
        resp = _get(client, f"/repos/{full_name}/readme")
    except RateLimited:
        raise
    except Exception as exc:  # noqa: BLE001
        log.debug("%s README 拉取失败：%s", full_name, exc)
        return None

    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
        raw = base64.b64decode(data.get("content", "")).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — tolerate arbitrary README encodings
        return None
    return raw[:README_MAX_CHARS].strip() or None


def fetch_meta(client: httpx.Client, full_name: str) -> RepoMeta | None:
    resp = _get(client, f"/repos/{full_name}")
    if resp.status_code == 404:
        log.info("%s 已删除或转为私有，跳过", full_name)
        return None
    if resp.status_code != 200:
        log.warning("%s 元信息返回 HTTP %d", full_name, resp.status_code)
        return None

    d = resp.json()
    readme = _fetch_readme(client, full_name)
    lic = d.get("license") or {}
    return RepoMeta(
        full_name=full_name,
        owner=(d.get("owner") or {}).get("login") or full_name.split("/")[0],
        topics=d.get("topics") or [],
        readme_head=readme,
        homepage=d.get("homepage") or None,
        license=lic.get("spdx_id") if isinstance(lic, dict) else None,
        created_at=d.get("created_at"),
    )


def _needs_enrich(conn: sqlite3.Connection, full_name: str) -> bool:
    row = conn.execute(
        "SELECT enriched_at FROM gh_repos WHERE full_name = ?", (full_name,)
    ).fetchone()
    if row is None:
        return True
    try:
        age = datetime.now(CST) - datetime.fromisoformat(row["enriched_at"])
    except ValueError:
        return True
    return age > timedelta(days=ENRICH_TTL_DAYS)


def _save(conn: sqlite3.Connection, meta: RepoMeta) -> None:
    conn.execute(
        """
        INSERT INTO gh_repos (full_name, owner, topics, readme_head, homepage,
                           license, created_at, enriched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(full_name) DO UPDATE SET
            owner=excluded.owner, topics=excluded.topics,
            readme_head=excluded.readme_head, homepage=excluded.homepage,
            license=excluded.license, created_at=excluded.created_at,
            enriched_at=excluded.enriched_at
        """,
        (
            meta.full_name,
            meta.owner,
            json.dumps(meta.topics, ensure_ascii=False),
            meta.readme_head,
            meta.homepage,
            meta.license,
            meta.created_at,
            now_iso(),
        ),
    )


def enrich_repos(full_names: list[str], force: bool = False) -> dict[str, int]:
    """Enrich repositories in batches while honoring fresh cache entries.

    Returns ``enriched``, ``cached``, ``failed``, and ``skipped_rate_limit`` counts.
    """
    stats = {"enriched": 0, "cached": 0, "failed": 0, "skipped_rate_limit": 0}

    with connect() as conn:
        todo = list(full_names) if force else [fn for fn in full_names if _needs_enrich(conn, fn)]
        stats["cached"] = len(full_names) - len(todo)
        if not todo:
            log.info("全部 %d 个仓库均有新鲜缓存，跳过富化", len(full_names))
            return stats

        if not settings.github_token:
            log.warning(
                "未配置 GITHUB_TOKEN，匿名限流 60 次/小时；本次需要富化 %d 个仓库，"
                "可能中途耗尽额度并降级",
                len(todo),
            )

        with httpx.Client(headers=_headers(), timeout=30.0, follow_redirects=True) as client:
            rate_limited = False
            for full_name in todo:
                if rate_limited:
                    stats["skipped_rate_limit"] += 1
                    continue
                try:
                    meta = fetch_meta(client, full_name)
                except RateLimited as exc:
                    log.warning(
                        "%s；剩余 %d 个仓库降级为仅用榜单描述写摘要",
                        exc,
                        len(todo) - stats["enriched"] - stats["failed"],
                    )
                    rate_limited = True
                    stats["skipped_rate_limit"] += 1
                    continue
                except Exception as exc:  # noqa: BLE001
                    log.warning("%s 富化失败：%s", full_name, exc)
                    stats["failed"] += 1
                    continue

                if meta is None:
                    stats["failed"] += 1
                    continue
                _save(conn, meta)
                stats["enriched"] += 1

    log.info(
        "富化完成：新增 %d，命中缓存 %d，失败 %d，因限流跳过 %d",
        stats["enriched"],
        stats["cached"],
        stats["failed"],
        stats["skipped_rate_limit"],
    )
    return stats
