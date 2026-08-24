"""Application configuration loaded from environment variables and ``.env``."""

from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CST = ZoneInfo("Asia/Shanghai")

NotifyMode = Literal["digest", "new_only"]
LlmResponseFormat = Literal["auto", "json_schema", "json_object", "text"]


class Settings(BaseSettings):
    """Runtime settings shared by all data sources."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_response_format: LlmResponseFormat = "auto"
    llm_summary_initial_tokens: Annotated[int, Field(ge=256, le=32768)] = 1500
    llm_summary_max_tokens: Annotated[int, Field(ge=256, le=32768)] = 6000

    github_token: str = ""
    github_periods: str = "daily,weekly,monthly"
    github_request_delay_seconds: Annotated[float, Field(ge=0)] = 2.0

    feishu_webhook_url: str = ""
    feishu_secret: str = ""

    hn_lag_days: Annotated[int, Field(ge=0)] = 2
    hn_min_points: Annotated[int, Field(ge=0)] = 100
    hn_top_n: Annotated[int, Field(ge=1, le=35)] = 25

    notify_mode: NotifyMode = "digest"
    expanded_per_section: Annotated[int, Field(ge=0)] = 5

    trend_sift_db_path: Path = PROJECT_ROOT / "data" / "trending.db"
    trend_sift_log_dir: Path = PROJECT_ROOT / "logs"

    @model_validator(mode="after")
    def validate_llm_token_range(self) -> "Settings":
        """Ensure the adaptive LLM token budget can only grow."""
        if self.llm_summary_max_tokens < self.llm_summary_initial_tokens:
            raise ValueError("LLM_SUMMARY_MAX_TOKENS 不能小于 LLM_SUMMARY_INITIAL_TOKENS")
        return self

    @property
    def periods(self) -> str:
        """Return configured GitHub Trending periods."""
        return self.github_periods

    @property
    def request_delay(self) -> float:
        """Return the delay between GitHub requests."""
        return self.github_request_delay_seconds

    @property
    def db_path(self) -> Path:
        """Return the SQLite database path."""
        return self.trend_sift_db_path

    @property
    def log_dir(self) -> Path:
        """Return the application log directory."""
        return self.trend_sift_log_dir

    def require_llm(self) -> None:
        """Raise a user-facing error when LLM credentials are incomplete."""
        missing = [
            name
            for name, value in (
                ("LLM_BASE_URL", self.llm_base_url),
                ("LLM_API_KEY", self.llm_api_key),
                ("LLM_MODEL", self.llm_model),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f".env 中缺少 {', '.join(missing)}，无法调用 LLM")

    def require_feishu(self) -> None:
        """Raise a user-facing error when the Feishu webhook is absent."""
        if not self.feishu_webhook_url:
            raise RuntimeError(".env 中缺少 FEISHU_WEBHOOK_URL，无法推送飞书")


settings = Settings()
