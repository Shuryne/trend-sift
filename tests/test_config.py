from pathlib import Path

import pytest
from pydantic import ValidationError

from trend_sift.core.config import Settings
from trend_sift.sources.github import config as github_config


def test_settings_defaults_without_env_file() -> None:
    settings = Settings()

    assert settings.periods == "daily,weekly,monthly"
    assert settings.request_delay == 2.0
    assert settings.hn_top_n == 25
    assert settings.notify_mode == "digest"
    assert settings.llm_summary_initial_tokens == 1500
    assert settings.llm_summary_max_tokens == 6000


def test_settings_use_public_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("GITHUB_PERIODS", "daily,weekly")
    monkeypatch.setenv("GITHUB_REQUEST_DELAY_SECONDS", "0.5")
    monkeypatch.setenv("TREND_SIFT_DB_PATH", "/tmp/trend-sift-test.db")

    settings = Settings()

    assert settings.llm_base_url == "https://llm.example/v1"
    assert settings.periods == "daily,weekly"
    assert settings.request_delay == 0.5
    assert settings.db_path == Path("/tmp/trend-sift-test.db")


def test_required_integrations_are_validated() -> None:
    settings = Settings(llm_base_url="", llm_api_key="", llm_model="")
    with pytest.raises(RuntimeError, match="LLM_BASE_URL"):
        settings.require_llm()
    with pytest.raises(RuntimeError, match="FEISHU_WEBHOOK_URL"):
        settings.require_feishu()


def test_settings_reject_invalid_hn_limit() -> None:
    with pytest.raises(ValidationError):
        Settings(hn_top_n=36)


def test_settings_reject_decreasing_llm_token_range() -> None:
    with pytest.raises(ValidationError, match="LLM_SUMMARY_MAX_TOKENS"):
        Settings(llm_summary_initial_tokens=3000, llm_summary_max_tokens=1500)


def test_github_periods_are_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_config.settings, "github_periods", "daily, monthly")
    assert github_config.period_list() == ["daily", "monthly"]

    monkeypatch.setattr(github_config.settings, "github_periods", "daily,yearly")
    with pytest.raises(ValueError, match="yearly"):
        github_config.period_list()

    monkeypatch.setattr(github_config.settings, "github_periods", "")
    with pytest.raises(ValueError, match="不能为空"):
        github_config.period_list()
