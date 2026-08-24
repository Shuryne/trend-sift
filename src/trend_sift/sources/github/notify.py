"""Build one Feishu card for each GitHub Trending period.

Period growth values are not comparable across daily, weekly, and monthly boards, so
cards remain separate. Leading items are expanded and the remainder are collapsible.
Transport concerns live in ``notifications.feishu``.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, NamedTuple

from ...core.config import NotifyMode, settings
from ...core.store import connect, now_iso
from ...notifications.feishu import fmt_num, send_card
from .config import Period
from .store import days_on_board

log = logging.getLogger(__name__)


class PeriodMeta(NamedTuple):
    title: str  # Card header title.
    growth_label: str  # Prefix for period-specific star growth.
    color: str  # Card theme color.


PERIOD_META: dict[Period, PeriodMeta] = {
    "daily": PeriodMeta("今日热门", "今日", "blue"),
    "weekly": PeriodMeta("本周热门", "本周", "wathet"),
    "monthly": PeriodMeta("本月热门", "本月", "turquoise"),
}
# Preserve the declaration order as the card delivery order.
PERIOD_ORDER: list[Period] = list(PERIOD_META)


@dataclass(slots=True)
class PushItem:
    full_name: str
    description: str | None
    summary_zh: str | None
    language: str | None
    stars: int
    stars_period: int
    days: int
    is_new: bool

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"


def _item_md(idx: int, item: PushItem, period: Period) -> dict[str, Any]:
    """Render one repository as a three-line Markdown card component."""
    stats = [f"⭐{fmt_num(item.stars)}"]
    if item.stars_period:
        stats.append(f"{PERIOD_META[period].growth_label} **+{fmt_num(item.stars_period)}**")
    if item.language:
        stats.append(item.language)
    if item.days > 1:
        stats.append(f"在榜 {item.days} 天")

    badge = " 🆕" if item.is_new else ""
    body = item.summary_zh or item.description or "（暂无描述）"

    return {
        "tag": "markdown",
        "content": (
            f"**{idx}. [{item.full_name}]({item.url})**{badge}\n"
            f"{body}\n"
            f"<font color='grey'>{'  ·  '.join(stats)}</font>"
        ),
    }


def build_card(items: list[PushItem], period: Period, snapshot_date: str) -> dict[str, Any]:
    """Build a period card with leading items expanded and all others collapsed."""
    meta = PERIOD_META[period]
    new_count = sum(1 for i in items if i.is_new)
    n = settings.expanded_per_section
    shown, rest = items[:n], items[n:]

    elements: list[dict[str, Any]] = [
        _item_md(i, it, period) for i, it in enumerate(shown, start=1)
    ]

    if rest:
        elements.append(
            {
                "tag": "collapsible_panel",
                "expanded": False,
                "header": {
                    "title": {
                        "tag": "markdown",
                        "content": f"<font color='grey'>展开剩余 {len(rest)} 个</font>",
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
                "elements": [_item_md(i, it, period) for i, it in enumerate(rest, start=n + 1)],
            }
        )

    footer = f"{snapshot_date}  ·  共 {len(items)} 个项目"
    if new_count:
        footer += f"  ·  🆕 {new_count} 个首次上榜"
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
            "text": {"tag": "plain_text", "content": "查看完整榜单"},
            "type": "default",
            "size": "small",
            # Card schema 2.0 expresses navigation through behaviors.
            "behaviors": [
                {
                    "type": "open_url",
                    "default_url": f"https://github.com/trending?since={period}",
                }
            ],
        }
    )

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "fill",
            # Provide useful conversation-list text instead of a generic card label.
            "summary": {"content": f"{meta.title}：{len(items)} 个项目（{snapshot_date}）"},
        },
        "header": {
            "template": meta.color,
            "title": {"tag": "plain_text", "content": meta.title},
        },
        "body": {
            "direction": "vertical",
            "padding": "12px",
            "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def items_by_period(
    snapshot_date: str, prompt_version: str, mode: NotifyMode | None = None
) -> dict[Period, list[PushItem]]:
    """Group pending items by period and order each group by period growth.

    A left join keeps repositories deliverable when summaries are unavailable.
    """
    mode = mode or settings.notify_mode
    out: dict[Period, list[PushItem]] = {}

    with connect() as conn:
        notified = {r["full_name"] for r in conn.execute("SELECT full_name FROM gh_notifications")}

        for period in PERIOD_ORDER:
            rows = conn.execute(
                """
                SELECT s.full_name, s.description, s.language,
                       s.stars, s.stars_period, m.summary_zh
                FROM gh_snapshots s
                LEFT JOIN gh_summaries m
                  ON m.full_name = s.full_name
                 AND m.prompt_version = ?
                WHERE s.snapshot_date = ? AND s.period = ?
                ORDER BY s.stars_period DESC, s.rank ASC
                """,
                (prompt_version, snapshot_date, period),
            ).fetchall()

            items = [
                PushItem(
                    full_name=r["full_name"],
                    description=r["description"],
                    summary_zh=r["summary_zh"],
                    language=r["language"],
                    stars=r["stars"] or 0,
                    stars_period=r["stars_period"] or 0,
                    days=days_on_board(conn, r["full_name"]),
                    is_new=r["full_name"] not in notified,
                )
                for r in rows
            ]

            if mode == "new_only":
                items = [i for i in items if i.is_new]
            if items:
                out[period] = items

    return out


def send_digest(grouped: dict[Period, list[PushItem]], snapshot_date: str) -> int:
    """Send one card per period and return the number delivered."""
    sent = 0
    for period in PERIOD_ORDER:
        items = grouped.get(period)
        if not items:
            continue
        size = send_card(build_card(items, period, snapshot_date), period)
        log.info("已发送 %s 卡片：%d 个项目，%.1f KB", period, len(items), size)
        sent += 1
        time.sleep(1)  # Stay below Feishu's bot rate limits.
    return sent


def mark_notified(grouped: dict[Period, list[PushItem]], snapshot_date: str) -> None:
    """Record the first delivery date used for later novelty badges."""
    seen: dict[str, None] = {}
    for items in grouped.values():
        for i in items:
            seen.setdefault(i.full_name)
    with connect() as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO gh_notifications (full_name, first_notified_at, snapshot_date)
            VALUES (?, ?, ?)
            """,
            [(fn, now_iso(), snapshot_date) for fn in seen],
        )
