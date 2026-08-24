"""HN 中文摘要：让 LLM 读正文，写一句给中文技术读者看的话。

prompt 和 GitHub 那边**刻意分开**，不是为了对称好看，而是摘要对象根本不同：

  GitHub —— 一个开源项目，要说清「是什么工具、解决什么问题」
  HN     —— 一条新闻/文章/讨论，要说清「发生了什么事、结论是什么」

用同一套 prompt 会让 HN 的摘要写成「本文介绍了……」这种没有信息量的句式。
版本号也各自独立（`hn-sum-v1` vs `sum-v1`），改一边不影响另一边的缓存。
"""

import logging
import sqlite3
from dataclasses import dataclass

from ...core.config import settings
from ...core.llm import LLMError, complete_json
from ...core.store import connect, now_iso

log = logging.getLogger(__name__)

PROMPT_VERSION = "hn-sum-v1"

# 喂给 LLM 的正文长度。入库时保留得更长，见 enrich.ARTICLE_MAX_CHARS。
ARTICLE_PROMPT_CHARS = 1800
LLM_SYSTEM = """你是一个技术资讯摘要助手，为中文技术读者介绍 Hacker News 上的热门帖子。

阅读给出的信息（标题、来源站点、正文摘录），写一句中文摘要：

- 25-45 字，一句话说清**发生了什么事 / 文章的核心结论是什么**
- 直接给结论和关键事实（数字、公司名、技术名），不要写「本文讨论了……」
- 面向中文技术读者，不要翻译腔，不要「值得关注」这类评语
- 不要复述标题里已有的信息，补充标题没说清的那部分
- 如果正文缺失或抓取失败，就只根据标题写，宁可短也不要编造细节

示例：
  好：AWS 计费系统故障，有用户账单从每月 5 美元暴涨到 17 亿美元，官方已确认为估算数据错误
  差：本文讨论了 AWS 出现的一个计费问题，引发了社区的广泛关注和讨论"""

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary_zh": {"type": "string"}},
    "required": ["summary_zh"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class Summary:
    object_id: str
    summary_zh: str | None
    model: str | None
    error: str | None = None


def _payload(title: str, site: str | None, story_text: str | None, article: str | None) -> str:
    parts = [f"标题：{title}"]
    if site:
        parts.append(f"来源：{site}")
    # 纯文本帖优先用 story_text —— 那就是作者自己写的正文，比抓来的更准
    body = story_text or article
    if body:
        label = "帖子正文" if story_text else "文章摘录"
        parts.append(f"{label}：\n{body[:ARTICLE_PROMPT_CHARS]}")
    else:
        parts.append("（正文未能获取，请仅根据标题作答）")
    return "\n".join(parts)


def summarize_one(
    object_id: str,
    title: str,
    site: str | None,
    story_text: str | None,
    article: str | None,
) -> Summary:
    """生成一条摘要。失败不抛异常 —— 推送时会退回只显示标题。"""
    try:
        data = complete_json(
            LLM_SYSTEM,
            _payload(title, site, story_text, article),
            SUMMARY_SCHEMA,
            max_tokens=settings.llm_summary_initial_tokens,
            max_tokens_cap=settings.llm_summary_max_tokens,
        )
    except LLMError as exc:
        log.warning("HN %s 摘要生成失败：%s", object_id, exc)
        return Summary(object_id, None, None, str(exc))

    return Summary(object_id, str(data["summary_zh"]).strip() or None, settings.llm_model)


def _load_inputs(
    conn: sqlite3.Connection, object_id: str
) -> tuple[str | None, str | None, str | None]:
    row = conn.execute(
        "SELECT story_text, article_head, site FROM hn_stories WHERE object_id = ?",
        (object_id,),
    ).fetchone()
    if row is None:
        return None, None, None
    return row["story_text"], row["article_head"], row["site"]


def _save_summary(conn: sqlite3.Connection, s: Summary) -> None:
    conn.execute(
        """
        INSERT INTO hn_summaries
            (object_id, summary_zh, prompt_version, model, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(object_id, prompt_version) DO UPDATE SET
            summary_zh=excluded.summary_zh,
            model=excluded.model,
            created_at=excluded.created_at
        """,
        (s.object_id, s.summary_zh, PROMPT_VERSION, s.model, now_iso()),
    )


def summarize_batch(stories: list[tuple[str, str]], force: bool = False) -> list[Summary]:
    """stories 是 [(object_id, title), ...]。

    已有当前 PROMPT_VERSION 摘要的跳过；上次失败（summary_zh 为空）的会重试，
    因为失败往往是限流或超时这类临时原因。
    """
    out: list[Summary] = []
    with connect() as conn:
        for object_id, title in stories:
            if not force:
                existing = conn.execute(
                    "SELECT summary_zh FROM hn_summaries "
                    "WHERE object_id = ? AND prompt_version = ?",
                    (object_id, PROMPT_VERSION),
                ).fetchone()
                if existing and existing["summary_zh"]:
                    continue
            story_text, article, site = _load_inputs(conn, object_id)
            s = summarize_one(object_id, title, site, story_text, article)
            _save_summary(conn, s)
            out.append(s)
            log.info("HN %s -> %s", object_id, s.summary_zh or f"（失败：{s.error}）")
    return out
