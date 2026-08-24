"""OpenAI-compatible LLM client with output fallback and content resampling.

Output modes are attempted from strongest to weakest: strict JSON Schema, JSON
object, and plain text with JSON extraction. SDK retries handle transport and server
errors; this module handles successful responses whose content is unusable.
"""

import json
import logging
import re
import time
from typing import Any

from openai import OpenAI

from .config import settings

log = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_BARE_JSON_RE = re.compile(r"\{.*\}", re.S)

# Process-local cache of the provider's confirmed output mode.
_mode: str | None = None


class LLMError(Exception):
    pass


class _FormatUnsupported(Exception):
    """Signal that the provider does not support the requested response format."""


class _EmptyContent(LLMError):
    """The provider returned HTTP 200 without visible assistant content."""

    def __init__(
        self,
        finish_reason: str | None,
        reasoning_tokens: int | None,
        max_tokens: int,
    ) -> None:
        self.finish_reason = finish_reason
        self.reasoning_tokens = reasoning_tokens
        self.max_tokens = max_tokens
        details = f"finish_reason={finish_reason}"
        if reasoning_tokens is not None:
            details += f", reasoning_tokens={reasoning_tokens}"
        super().__init__(f"模型返回空内容（{details}）；当前 max_tokens={max_tokens}")


def _client() -> OpenAI:
    settings.require_llm()
    return OpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        timeout=90.0,
        max_retries=2,
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from plain text or a Markdown code block."""
    for pattern in (_JSON_BLOCK_RE, _BARE_JSON_RE):
        m = pattern.search(text)
        if m:
            candidate = m.group(1) if pattern is _JSON_BLOCK_RE else m.group(0)
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
    raise LLMError(f"响应中未找到合法 JSON：{text[:200]!r}")


def _response_format(mode: str, schema: dict[str, Any]) -> dict[str, Any] | None:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": "result", "strict": True, "schema": schema},
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def _attempt(
    client: OpenAI,
    mode: str,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    max_tokens: int,
) -> dict[str, Any]:
    """Send one request and parse its response.

    Raises:
        _FormatUnsupported: The provider rejects the response format.
        LLMError: The request or returned content is unusable.
    """
    kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    rf = _response_format(mode, schema)
    if rf:
        kwargs["response_format"] = rf

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "response_format" in msg:
            raise _FormatUnsupported(msg) from exc
        raise LLMError(f"LLM 调用失败（模式 {mode}）：{msg}") from exc

    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    if not content:
        # Empty content does not imply an unsupported format. Reasoning tokens share
        # the output budget and can exhaust it before visible content is produced.
        reasoning = getattr(
            getattr(resp.usage, "completion_tokens_details", None),
            "reasoning_tokens",
            None,
        )
        raise _EmptyContent(choice.finish_reason, reasoning, max_tokens)

    try:
        data = json.loads(content) if mode != "text" else _extract_json(content)
    except json.JSONDecodeError:
        data = _extract_json(content)

    missing = [k for k in schema.get("required", []) if k not in data]
    if missing:
        raise LLMError(f"响应缺少必填字段 {missing}：{data}")
    return data


def complete_json(
    system: str,
    user: str,
    schema: dict[str, Any],
    max_tokens: int = 3000,
    max_tokens_cap: int | None = None,
    attempts: int = 3,
) -> dict[str, Any]:
    """Request schema-compatible JSON with format fallback and resampling.

    Providers do not enforce the schema in JSON-object or text mode, so the schema
    is included in the prompt and required fields are validated after parsing.
    """
    global _mode

    client = _client()
    # Always include the schema because it is the only constraint in weaker modes.
    sys_prompt = (
        f"{system}\n\n"
        f"你必须只返回一个 JSON 对象，不要有任何解释或 markdown 代码块。"
        f"JSON 必须严格符合以下 schema：\n{json.dumps(schema, ensure_ascii=False)}"
    )
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user},
    ]

    if _mode:
        candidates = [_mode]
    elif settings.llm_response_format == "auto":
        candidates = ["json_schema", "json_object", "text"]
    else:
        candidates = [settings.llm_response_format]
    last_error: Exception | None = None
    token_cap = max_tokens if max_tokens_cap is None else max_tokens_cap
    if token_cap < max_tokens:
        raise ValueError("max_tokens_cap 不能小于 max_tokens")

    for mode in candidates:
        assert mode is not None
        data: dict[str, Any] | None = None

        request_max_tokens = max_tokens
        for attempt in range(1, attempts + 1):
            try:
                data = _attempt(client, mode, messages, schema, request_max_tokens)
                break
            except _FormatUnsupported as exc:
                # Falling back does not consume a content retry.
                log.info("模式 %s 不被服务商支持，降级", mode)
                last_error = exc
                break
            except _EmptyContent as exc:
                last_error = exc
                if attempt == attempts:
                    raise LLMError(f"重试 {attempts} 次仍失败：{exc}") from exc
                if exc.finish_reason == "length" and request_max_tokens < token_cap:
                    next_tokens = min(request_max_tokens * 2, token_cap)
                    log.warning(
                        "第 %d/%d 次输出额度耗尽，max_tokens 从 %d 提升到 %d 后重试",
                        attempt,
                        attempts,
                        request_max_tokens,
                        next_tokens,
                    )
                    request_max_tokens = next_tokens
                else:
                    log.warning(
                        "第 %d/%d 次模型返回空内容，按原额度重采样：%s",
                        attempt,
                        attempts,
                        exc,
                    )
                time.sleep(attempt)
            except LLMError as exc:
                last_error = exc
                if attempt == attempts:
                    raise LLMError(f"重试 {attempts} 次仍失败：{exc}") from exc
                log.warning("第 %d/%d 次内容不可用，重采样重试：%s", attempt, attempts, exc)
                time.sleep(attempt)  # Add a small delay before resampling.

        if data is None:
            continue  # Try the next mode after a format rejection.

        if _mode != mode:
            log.info("LLM 结构化输出模式已确定为：%s", mode)
            _mode = mode
        return data

    raise LLMError(f"所有输出模式均失败，最后一个错误：{last_error}")


def probe() -> str:
    """Probe and return the structured-output mode used by the doctor command."""
    complete_json(
        system="你是一个测试助手。",
        user='返回 {"ok": true}',
        schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        max_tokens=512,
    )
    assert _mode is not None
    return _mode
