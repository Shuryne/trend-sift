"""Feishu bot transport for signing, sending, alerting, and payload sizing.

Data sources own their card layouts because GitHub and Hacker News expose different
fields. This module only handles the shared delivery boundary.
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

# Reserve 1 KB below Feishu's 20 KB custom-bot payload limit.
CARD_BUDGET_KB = 19.0


class NotifyError(Exception):
    pass


def payload_kb(payload: dict[str, Any]) -> float:
    """Return the UTF-8 JSON payload size in kilobytes."""
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) / 1024


def fmt_num(n: int) -> str:
    """Compact large numbers using the Chinese ten-thousand unit."""
    if n >= 10000:
        return f"{n / 10000:.1f}万".replace(".0万", "万")
    return f"{n:,}"


def _sign(timestamp: str, secret: str) -> str:
    """Generate the Feishu HMAC-SHA256 signature for a timestamp."""
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
    # Feishu returns HTTP 200 for business errors, so inspect the response code.
    if data.get("code", 0) != 0:
        raise NotifyError(f"飞书返回错误 code={data.get('code')} msg={data.get('msg')}")


def send_card(card: dict[str, Any], label: str) -> float:
    """Send one card and return its payload size in kilobytes."""
    payload = {"msg_type": "interactive", "card": card}
    size = payload_kb(payload)
    if size > CARD_BUDGET_KB:
        # This warning usually indicates an unexpectedly long summary.
        log.warning("%s 卡片 %.1f KB，逼近自定义机器人 20 KB 上限", label, size)
    post(payload)
    return size


def send_alert(title: str, message: str) -> None:
    """Send a best-effort alert using the broadly compatible card 1.0 schema."""
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
    except Exception as exc:  # noqa: BLE001 — do not mask the original failure
        log.error("发送告警失败：%s", exc)
