"""中文摘要生成：让 LLM 读 README，写一句给中文技术读者看的话。

**不做任何主题筛选** —— 榜单上有什么就推什么。这里曾经有一层「是否 AI 相关」
的判定，删除的理由见 README「为什么去掉了 AI 筛选」。

摘要按 PROMPT_VERSION 存储：改 prompt 时 bump 版本号，旧摘要原样保留，
可以直接 SQL 对比同一批项目在新旧 prompt 下的差异。
"""

import json
import logging
import sqlite3
from dataclasses import dataclass

from ...core.config import settings
from ...core.llm import LLMError, complete_json
from ...core.store import connect, now_iso

log = logging.getLogger(__name__)

PROMPT_VERSION = "sum-v1"

# 喂给 LLM 的 README 长度。入库时保留得更长，见 enrich.README_MAX_CHARS。
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
    """生成一条摘要。失败不抛异常 —— 推送时会退回用榜单原始描述。"""
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


def summarize_batch(repos: list[tuple[str, str | None]], force: bool = False) -> list[Summary]:
    """repos 是 [(full_name, description), ...]。

    已有当前 PROMPT_VERSION 摘要的跳过；上次失败（summary_zh 为空）的会重试，
    因为失败往往是限流或超时这类临时原因。
    """
    out: list[Summary] = []
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
            s = summarize_one(full_name, description, topics, readme)
            _save_summary(conn, s)
            out.append(s)
            log.info("%s -> %s", full_name, s.summary_zh or f"（失败：{s.error}）")
    return out
