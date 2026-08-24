"""LLM 客户端：结构化输出三级降级 + 同模式内重采样重试。

输出模式按能力从强到弱依次尝试，首次探测后记住结果：

    1. json_schema strict  —— 服务端保证结构，最可靠
    2. json_object         —— 服务端保证是合法 JSON，字段靠 prompt 约束
    3. 纯文本 + 提取       —— 从 markdown 代码块或裸文本里抠 JSON

两条不变量（背景与实测数据见 README「摘要生成」「工程注意事项」）：

  - 重试不跨模式。「格式不支持」降级模式且不消耗重试次数；「内容为空」
    耗尽次数直接抛，绝不降级 —— 降级会掩盖真正的原因。
  - 这层重试与 SDK 的 `max_retries` 正交：后者管连接错误和 429/5xx，
    这里管「HTTP 200 但内容不可用」。
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

# 模块级缓存：None=未探测，其余为已确认可用的模式
_mode: str | None = None


class LLMError(Exception):
    pass


class _FormatUnsupported(Exception):
    """服务商不支持该 response_format —— 换模式重来，不消耗重试次数。"""


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
    """从可能包着 markdown 代码块或解释文字的响应里抠出 JSON。"""
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
    """发一次请求并解析。

    Raises:
        _FormatUnsupported: 服务商不支持该 response_format。
        LLMError: 其余任何失败（调用出错、空正文、JSON 非法、缺必填字段）。
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
        # 关键：空内容不是「该模式不支持」，抛 LLMError 而非 _FormatUnsupported。
        # 推理模型的 reasoning token 也计入 max_tokens，额度太小会导致推理
        # 耗尽、正文为空 —— 此时降级模式只会掩盖真正的原因。
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
    """要求模型返回符合 schema 的 JSON，自动按能力降级，内容不可用时重采样。

    schema 在 json_object / 纯文本模式下不会被服务端强制，但仍会拼进
    system prompt 作为约束，并在返回后做必填字段校验。
    """
    global _mode

    client = _client()
    # schema 始终告知模型，弱模式下这是唯一的结构约束来源
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
                # 降级到下一档，不算失败，也不消耗重试次数（重试也还是不支持）
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
                time.sleep(attempt)  # 1s、2s……给限流一点缓冲

        if data is None:
            continue  # 该模式不被支持，试下一档

        if _mode != mode:
            log.info("LLM 结构化输出模式已确定为：%s", mode)
            _mode = mode
        return data

    raise LLMError(f"所有输出模式均失败，最后一个错误：{last_error}")


def probe() -> str:
    """探测并返回可用的结构化输出模式，用于 doctor 命令。"""
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
