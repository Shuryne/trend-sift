"""Hacker News 的飞书卡片：一天一张。

和 GitHub 那边最大的结构差异：**不按周期拆卡**。GitHub 有 daily/weekly/monthly
三个并存的榜，数字不可比所以必须分开；HN 一天就一个榜，一张卡装完。

卡片里同时给两个链接 —— 标题跳原文，「N 评论」跳 HN 讨论页。这不是冗余：
HN 的价值常常在评论区而不是原文，只给一个链接会漏掉一半信息。
"""

import logging
from dataclasses import dataclass
from typing import Any

from ...core.config import NotifyMode, settings
from ...core.store import connect, now_iso
from ...notifications.feishu import fmt_num, send_card
from . import models

log = logging.getLogger(__name__)

CARD_TITLE = "Hacker News 热帖"
CARD_COLOR = "orange"  # HN 的品牌色，和 GitHub 那几张蓝绿色卡片区分开


@dataclass(slots=True)
class PushItem:
    object_id: str
    title: str
    url: str | None
    author: str
    summary_zh: str | None
    points: int
    num_comments: int
    is_new: bool

    @property
    def hn_url(self) -> str:
        return models.hn_url(self.object_id)

    @property
    def target_url(self) -> str:
        return models.target_url(self.url, self.object_id)


def _item_md(idx: int, item: PushItem) -> dict[str, Any]:
    """单条 = 一个 markdown 组件，三行：标题 / 摘要 / 灰色数据。

    排版刻意和 GitHub 卡片保持一致，两张卡放在同一个群里才不会显得是两个系统。
    """
    stats = [
        f"▲{fmt_num(item.points)}",
        f"[{item.num_comments} 评论]({item.hn_url})",
    ]
    if item.author:
        stats.append(f"@{item.author}")

    badge = " 🆕" if item.is_new else ""
    body = item.summary_zh or "（暂无摘要）"

    return {
        "tag": "markdown",
        "content": (
            f"**{idx}. [{item.title}]({item.target_url})**{badge}\n"
            f"{body}\n"
            f"<font color='grey'>{'  ·  '.join(stats)}</font>"
        ),
    }


def build_card(items: list[PushItem], snapshot_date: str) -> dict[str, Any]:
    """前 N 名展开 + 折叠面板装下剩余的全部条目。结构同 GitHub 卡片。"""
    new_count = sum(1 for i in items if i.is_new)
    n = settings.expanded_per_section
    shown, rest = items[:n], items[n:]

    elements: list[dict[str, Any]] = [_item_md(i, it) for i, it in enumerate(shown, start=1)]

    if rest:
        elements.append(
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                    "title": {
                        "tag": "markdown",
                        "content": f"<font color='grey'>展开剩余 {len(rest)} 条</font>",
                    },
                    "vertical_align": "center",
                    "icon": {
                        "tag": "standard_icon",
                        "token": "down-small-ccm_outlined",
                        "color": "grey",
                        "size": "16px 16px",
                    },
                    "icon_position": "follow_text",
                    "icon_expanded_angle": -180,
                },
                "vertical_spacing": "8px",
                "padding": "4px 0px 4px 0px",
                "elements": [_item_md(i, it) for i, it in enumerate(rest, start=n + 1)],
            }
        )

    footer = f"{snapshot_date} (UTC)  ·  共 {len(items)} 条"
    if new_count:
        footer += f"  ·  🆕 {new_count} 条首次上榜"
    elements.append({"tag": "hr"})
    elements.append(
        {
            "tag": "markdown",
            "text_size": "notation",
            "content": f"<font color='grey'>{footer}</font>",
        }
    )
    elements.append(
        {
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看当日完整榜单"},
            "type": "default",
            "size": "small",
            "behaviors": [
                {
                    "type": "open_url",
                    "default_url": f"https://news.ycombinator.com/front?day={snapshot_date}",
                }
            ],
        }
    )

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "fill",
            "summary": {"content": f"{CARD_TITLE}：{len(items)} 条（{snapshot_date}）"},
        },
        "header": {
            "template": CARD_COLOR,
            "title": {"tag": "plain_text", "content": CARD_TITLE},
        },
        "body": {
            "direction": "vertical",
            "padding": "12px",
            "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def items_for(
    snapshot_date: str, prompt_version: str, mode: NotifyMode | None = None
) -> list[PushItem]:
    """当日待推送条目，按分数降序。

    摘要用 LEFT JOIN 而非 INNER JOIN：榜上有什么就推什么，摘要只是锦上添花。
    LLM 挂了或某条超时，该条目仍然出现在卡片里，只是没有中文摘要。
    """
    mode = mode or settings.notify_mode

    with connect() as conn:
        notified = {r["object_id"] for r in conn.execute("SELECT object_id FROM hn_notifications")}

        rows = conn.execute(
            """
            SELECT s.object_id, s.title, s.url, s.author,
                   s.points, s.num_comments, m.summary_zh
            FROM hn_snapshots s
            LEFT JOIN hn_summaries m
              ON m.object_id = s.object_id
             AND m.prompt_version = ?
            WHERE s.snapshot_date = ?
            ORDER BY s.points DESC, s.rank ASC
            """,
            (prompt_version, snapshot_date),
        ).fetchall()

        items = [
            PushItem(
                object_id=r["object_id"],
                title=r["title"],
                url=r["url"],
                author=r["author"] or "",
                summary_zh=r["summary_zh"],
                points=r["points"] or 0,
                num_comments=r["num_comments"] or 0,
                is_new=r["object_id"] not in notified,
            )
            for r in rows
        ]

    if mode == "new_only":
        items = [i for i in items if i.is_new]
    return items


def send_digest(items: list[PushItem], snapshot_date: str) -> int:
    """发一张卡片。返回成功发送的卡片数（0 或 1），与 GitHub 侧签名对齐。"""
    if not items:
        return 0
    size = send_card(build_card(items, snapshot_date), "hn")
    log.info("已发送 HN 卡片：%d 条，%.1f KB", len(items), size)
    return 1


def mark_notified(items: list[PushItem], snapshot_date: str) -> None:
    """记录首次推送时间。同一帖子只记一次，用于后续的 🆕 标记。"""
    with connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO hn_notifications (object_id, first_notified_at, snapshot_date)
            VALUES (?, ?, ?)
            """,
            [(i.object_id, now_iso(), snapshot_date) for i in items],
        )
