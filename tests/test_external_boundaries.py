from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest
from tenacity import wait_none

from trend_sift.core import llm
from trend_sift.core.config import settings
from trend_sift.notifications import feishu
from trend_sift.sources.github import fetcher as github_fetcher
from trend_sift.sources.github import pipeline as github_pipeline
from trend_sift.sources.hacker_news import enrich as hn_enrich
from trend_sift.sources.hacker_news import fetcher as hn_fetcher
from trend_sift.sources.hacker_news import pipeline as hn_pipeline


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_github_fetch_uses_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["since"] == "daily"
        return httpx.Response(200, text="<html>ok</html>", request=request)

    with client_with(handler) as client:
        page = github_fetcher.fetch_period(client, "daily", "2026-08-24")
    assert page.http_status == 200
    assert page.html == "<html>ok</html>"


def test_github_rate_limit_is_not_treated_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="rate limited", request=request)

    with client_with(handler) as client, pytest.raises(github_fetcher.FetchError, match="403"):
        github_fetcher.fetch_period(client, "weekly", "2026-08-24")


def test_network_timeout_is_retried_without_real_wait() -> None:
    attempts = 0
    cast(Any, github_fetcher._get).retry.wait = wait_none()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timeout", request=request)

    with client_with(handler) as client, pytest.raises(httpx.ReadTimeout):
        github_fetcher._get(client, "https://example.test", {})
    assert attempts == 3


def test_hn_server_error_is_retried() -> None:
    attempts = 0
    cast(Any, hn_fetcher._get).retry.wait = wait_none()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, request=request)

    with client_with(handler) as client, pytest.raises(httpx.HTTPStatusError):
        hn_fetcher._get(client, "https://example.test")
    assert attempts == 3


def test_feishu_success_and_business_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "feishu_webhook_url", "https://example.test/hook")
    monkeypatch.setattr(settings, "feishu_secret", "")

    def success(url: str, **_: object) -> httpx.Response:
        return httpx.Response(200, json={"code": 0}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", success)
    feishu.post({"msg_type": "text"})

    def rejected(url: str, **_: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 19001, "msg": "bad card"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", rejected)
    with pytest.raises(feishu.NotifyError, match="19001"):
        feishu.post({"msg_type": "interactive"})


def test_llm_text_fallback_extracts_json() -> None:
    assert llm._extract_json('Result: ```json\n{"summary": "ok"}\n```') == {"summary": "ok"}
    with pytest.raises(llm.LLMError, match="未找到合法 JSON"):
        llm._extract_json("no structured output")


def test_llm_probe_reserves_tokens_for_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_complete_json(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        llm._mode = "json_object"
        return {"ok": True}

    monkeypatch.setattr(llm, "_mode", None)
    monkeypatch.setattr(llm, "complete_json", fake_complete_json)

    assert llm.probe() == "json_object"
    assert captured["max_tokens"] == 512


def test_llm_increases_budget_only_after_length_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets: list[int] = []

    def fake_attempt(*args: object) -> dict[str, object]:
        budget = cast(int, args[-1])
        budgets.append(budget)
        if len(budgets) == 1:
            raise llm._EmptyContent("length", budget, budget)
        return {"summary": "ok"}

    monkeypatch.setattr(llm, "_mode", "json_object")
    monkeypatch.setattr(llm, "_client", lambda: object())
    monkeypatch.setattr(llm, "_attempt", fake_attempt)
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)

    result = llm.complete_json(
        "system",
        "user",
        {"required": ["summary"]},
        max_tokens=1500,
        max_tokens_cap=6000,
    )

    assert result == {"summary": "ok"}
    assert budgets == [1500, 3000]


def test_hn_article_fetch_retries_timeout_and_reports_reason() -> None:
    attempts = 0
    cast(Any, hn_enrich._get_article).retry.wait = wait_none()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timeout", request=request)

    with client_with(handler) as client:
        result = hn_enrich.fetch_article(client, "https://example.test/article")

    assert attempts == 2
    assert result.text is None
    assert result.outcome == "timeout"


def test_hn_article_fetch_classifies_non_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"binary",
            headers={"content-type": "application/pdf"},
            request=request,
        )

    with client_with(handler) as client:
        result = hn_enrich.fetch_article(client, "https://example.test/paper")

    assert result.text is None
    assert result.outcome == "non_html"


def test_dry_run_never_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_pipeline, "init_db", lambda: None)
    monkeypatch.setattr(github_pipeline, "items_by_period", lambda *_: {"daily": [object()]})
    monkeypatch.setattr(hn_pipeline, "init_db", lambda: None)
    monkeypatch.setattr(hn_pipeline, "items_for", lambda *_: [object()])

    def unexpected_send(*_: object) -> int:
        raise AssertionError("dry-run attempted to send")

    monkeypatch.setattr(github_pipeline, "send_digest", unexpected_send)
    monkeypatch.setattr(hn_pipeline, "send_digest", unexpected_send)

    assert github_pipeline.run_notify("2026-08-24", dry_run=True)["cards"] == 0
    assert hn_pipeline.run_notify("2026-08-22", dry_run=True)["cards"] == 0
