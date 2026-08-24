"""GitHub Trending 的飞书卡片：今日 / 本周 / 本月各一张。

Transport concerns such as signing and HTTP delivery live in notifications/feishu.py.

三条设计决策，理由见 README「推送格式」及其子节：

  - **按周期拆卡**，不合并成一张列表。`stars_period` 是「该周期内新增的
    星数」，日榜的 +800 和月榜的 +34,000 不可比，混排数字就失去意义。
  - **前 N 名展开、其余收进折叠面板**，但全部项目都在卡片里，不做截断。
  - **正常卡片用 JSON 2.0，告警卡留在 1.0**。注意 2.0 对未知属性直接报错
    而非静默忽略，改字段名必须对齐官方文档。
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
    title: str  # 卡片 header 标题
    growth_label: str  # 增量星数的文案前缀，如「今日 +1,126」
    color: str  # 卡片主题色


PERIOD_META: dict[Period, PeriodMeta] = {
    "daily": PeriodMeta("今日热门", "今日", "blue"),
    "weekly": PeriodMeta("本周热门", "本周", "wathet"),
    "monthly": PeriodMeta("本月热门", "本月", "turquoise"),
}
# 发送顺序即 PERIOD_META 的声明顺序，不再单独维护一份列表
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
    """单个项目 = 一个 markdown 组件，三行：标题 / 摘要 / 灰色数据。

    数据行放在最后而不是紧跟标题：条目之间已经没有分割线了（`hr` 太占地方），
    末尾这行灰字正好充当视觉分隔，把上一条的摘要和下一条的标题隔开。
    """
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
    """一个周期一张卡片：前 N 名展开 + 折叠面板装下剩余的全部项目。

    卡片顶部的彩色 header 已经写明是哪个周期，所以卡内不再重复一个标题，
    「查看完整榜单」按钮保留 —— 它是唯一跳到 GitHub 原榜的入口。
    """
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
            # 2.0 取消了 action 容器和 button.url，跳转统一走 behaviors
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
            # 会话列表里的消息预览文案，不写的话只显示「[卡片]」
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
    """按榜单周期分组返回待推送项目，每组内按该周期的增量星数降序。

    stars_period 直接取自对应周期的快照行，不做跨周期聚合（理由见模块注释）。

    摘要用 LEFT JOIN 而非 INNER JOIN：榜单上有什么就推什么，摘要只是锦上添花。
    LLM 挂了或某条超时，该项目仍然出现在卡片里，只是退回显示英文原始描述。
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
    """每个周期发一张卡片。返回成功发送的卡片数。"""
    sent = 0
    for period in PERIOD_ORDER:
        items = grouped.get(period)
        if not items:
            continue
        size = send_card(build_card(items, period, snapshot_date), period)
        log.info("已发送 %s 卡片：%d 个项目，%.1f KB", period, len(items), size)
        sent += 1
        time.sleep(1)  # 避免触发飞书频率限制（5 次/秒、100 次/分）
    return sent


def mark_notified(grouped: dict[Period, list[PushItem]], snapshot_date: str) -> None:
    """记录首次推送时间。同一仓库只记一次，用于后续的 🆕 标记。"""
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
