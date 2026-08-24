"""Generate concise Chinese summaries from repository metadata and README excerpts.

Summaries are versioned by prompt so revisions can be recomputed without replacing
previous results. Every trending repository is included without topic filtering.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass

from ...core.concurrency import map_concurrently
from ...core.config import settings
from ...core.llm import LLMError, complete_json
from ...core.store import connect, now_iso

log = logging.getLogger(__name__)

PROMPT_VERSION = "sum-v1"

# Limit prompt context while retaining a larger excerpt in storage.
README_PROMPT_CHARS = 1500

LLM_SYSTEM = """你是一个技术项目摘要助手，为中文技术读者介绍 GitHub 上的开源项目。

阅读给出的项目信息（名称、描述、topics、README 摘录），写一句中文摘要：

- 20-40 字，一句话说清**项目是什么、解决什么问题**
- 有明显亮点（性能数字、独特设计、对标的知名产品）就带上一个，没有就不要硬凑
- 面向中文技术读者，直接说事，不要翻译腔，不要「本项目是一个……」这类套话
- 不要复述项目名，不要写「该项目」开头
- 不做任何价值判断，不写「值得关注」「非常强大」这类评语

示例：
  好：基于 Rust 的终端文件管理器，支持异步 IO 和全键盘操作，启动只要 10ms
  差：这是一个非常优秀的终端文件管理器项目，值得大家关注和学习"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary_zh": {"type": "string"}},
    "required": ["summary_zh"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class Summary:
    full_name: str
    summary_zh: str | None
    model: str | None
    error: str | None = None


def _payload(full_name: str, description: str | None, topics: list[str], readme: str | None) -> str:
    parts = [f"项目：{full_name}"]
    if description:
        parts.append(f"描述：{description}")
    if topics:
        parts.append(f"Topics：{', '.join(topics)}")
    if readme:
        parts.append(f"README 摘录：\n{readme[:README_PROMPT_CHARS]}")
    return "\n".join(parts)


def summarize_one(
    full_name: str,
    description: str | None,
    topics: list[str],
    readme: str | None,
) -> Summary:
    """Generate one summary, returning an empty result for graceful fallback."""
    try:
        data = complete_json(
            LLM_SYSTEM,
            _payload(full_name, description, topics, readme),
            SUMMARY_SCHEMA,
            max_tokens=settings.llm_summary_initial_tokens,
            max_tokens_cap=settings.llm_summary_max_tokens,
        )
    except LLMError as exc:
        log.warning("%s 摘要生成失败：%s", full_name, exc)
        return Summary(full_name, None, None, str(exc))

    return Summary(full_name, str(data["summary_zh"]).strip() or None, settings.llm_model)


def _load_inputs(conn: sqlite3.Connection, full_name: str) -> tuple[list[str], str | None]:
    row = conn.execute(
        "SELECT topics, readme_head FROM gh_repos WHERE full_name = ?", (full_name,)
    ).fetchone()
    if row is None:
        return [], None
    try:
        topics = json.loads(row["topics"] or "[]")
    except json.JSONDecodeError:
        topics = []
    return topics, row["readme_head"]


def _save_summary(conn: sqlite3.Connection, s: Summary) -> None:
    conn.execute(
        """
        INSERT INTO gh_summaries
            (full_name, summary_zh, prompt_version, model, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(full_name, prompt_version) DO UPDATE SET
            summary_zh=excluded.summary_zh,
            model=excluded.model,
            created_at=excluded.created_at
        """,
        (s.full_name, s.summary_zh, PROMPT_VERSION, s.model, now_iso()),
    )


def _summarize_job(
    job: tuple[str, str | None, list[str], str | None],
) -> Summary:
    return summarize_one(*job)


def summarize_batch(repos: list[tuple[str, str | None]], force: bool = False) -> list[Summary]:
    """Summarize ``(full_name, description)`` pairs not already cached.

    Empty cached results are retried because most failures are transient.
    """
    jobs: list[tuple[str, str | None, list[str], str | None]] = []
    with connect() as conn:
        for full_name, description in repos:
            if not force:
                existing = conn.execute(
                    "SELECT summary_zh FROM gh_summaries "
                    "WHERE full_name = ? AND prompt_version = ?",
                    (full_name, PROMPT_VERSION),
                ).fetchone()
                if existing and existing["summary_zh"]:
                    continue
            topics, readme = _load_inputs(conn, full_name)
            jobs.append((full_name, description, topics, readme))

    log.info(
        "开始生成 %d 条 GitHub 摘要，并发数 %d",
        len(jobs),
        settings.llm_summary_concurrency,
    )
    out = map_concurrently(
        _summarize_job,
        jobs,
        max_workers=settings.llm_summary_concurrency,
    )

    with connect() as conn:
        for s in out:
            _save_summary(conn, s)
            log.info("%s -> %s", s.full_name, s.summary_zh or f"（失败：{s.error}）")
    return out
