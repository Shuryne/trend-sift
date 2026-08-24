"""飞书群机器人的传输层：签名、发送、告警、体积计算。

这里只管「怎么把一张卡片发出去」，不管「卡片长什么样」——
后者是各数据源自己的事（github/notify.py、hn/notify.py），因为卡片布局
和字段强绑定：GitHub 卡显示星数和语言，HN 卡显示分数和评论数，
没有共同的排版可言。

自定义机器人的两条硬限制（各数据源的卡片都已避开）：
发不了图片；交互组件只能用 open_url。
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..core.config import settings

log = logging.getLogger(__name__)

# 自定义机器人的请求体上限是 20 KB，留 1 KB 给签名字段和编码波动。
# 单榜最多 25 条约 8 KB，正常永远够用 —— 这个值只用来在异常时预警。
CARD_BUDGET_KB = 19.0


class NotifyError(Exception):
    pass


def payload_kb(payload: dict[str, Any]) -> float:
    """请求体体积（KB）。httpx 用 ensure_ascii=False 编码，中文按 UTF-8 算 3 字节。"""
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) / 1024


def fmt_num(n: int) -> str:
    """大数字压成「52.8万」。榜首动辄 50 万星，逐位显示既占地方又没人真的去读。"""
    if n >= 10000:
        return f"{n / 10000:.1f}万".replace(".0万", "万")
    return f"{n:,}"


def _sign(timestamp: str, secret: str) -> str:
    """飞书签名：以 "{timestamp}\\n{secret}" 为密钥，对空消息体做 HMAC-SHA256。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=15),
    reraise=True,
)
def post(payload: dict[str, Any]) -> None:
    settings.require_feishu()
    if settings.feishu_secret:
        ts = str(int(time.time()))
        payload = {**payload, "timestamp": ts, "sign": _sign(ts, settings.feishu_secret)}

    resp = httpx.post(settings.feishu_webhook_url, json=payload, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
    # 飞书对业务错误也返回 HTTP 200，必须查 code
    if data.get("code", 0) != 0:
        raise NotifyError(f"飞书返回错误 code={data.get('code')} msg={data.get('msg')}")


def send_card(card: dict[str, Any], label: str) -> float:
    """发送一张卡片，返回其体积（KB）。超预算时先警告再发，别被飞书静默拒掉。"""
    payload = {"msg_type": "interactive", "card": card}
    size = payload_kb(payload)
    if size > CARD_BUDGET_KB:
        # 真触发说明摘要异常地长
        log.warning("%s 卡片 %.1f KB，逼近自定义机器人 20 KB 上限", label, size)
    post(payload)
    return size


def send_alert(title: str, message: str) -> None:
    """发送告警。任务静默失败是最糟的情况，必须让群里看得见。

    这张卡故意留在 1.0 schema：2.0 要求客户端 7.20+，而告警是最不该挑渲染
    环境的一条消息，它只有一段纯文本，1.0 完全够用。
    """
    if not settings.feishu_webhook_url:
        log.error("未配置飞书 webhook，无法发送告警：%s", message)
        return
    try:
        post(
            {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "template": "red",
                        "title": {"tag": "plain_text", "content": f"⚠️ {title}"},
                    },
                    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": message}}],
                },
            }
        )
    except Exception as exc:  # noqa: BLE001 — 告警失败不能再抛，否则掩盖原始错误
        log.error("发送告警失败：%s", exc)
