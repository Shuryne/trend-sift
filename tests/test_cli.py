import argparse
from pathlib import Path

import pytest

from trend_sift.cli import commands
from trend_sift.cli.main import build_parser
from trend_sift.core import llm


def test_public_command_names_and_dates() -> None:
    args = build_parser().parse_args(
        ["run", "--dry-run", "--github-date", "2026-08-24", "--hn-date", "2026-08-22"]
    )
    assert args.command == "run"
    assert args.dry_run is True
    assert args.github_date == "2026-08-24"
    assert args.hn_date == "2026-08-22"


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["github", "fetch", "--date", "2026-08-24"], "github"),
        (["hacker-news", "show", "--date", "2026-08-22"], "hacker-news"),
        (["github", "history", "openai/codex"], "github"),
    ],
)
def test_source_commands_parse(argv: list[str], command: str) -> None:
    assert build_parser().parse_args(argv).command == command


def test_removed_short_source_name_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["gh", "fetch"])


def test_doctor_allows_missing_feishu(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(commands.settings, "llm_base_url", "https://llm.example/v1")
    monkeypatch.setattr(commands.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(commands.settings, "llm_model", "test-model")
    monkeypatch.setattr(commands.settings, "feishu_webhook_url", "")
    monkeypatch.setattr(commands.settings, "feishu_secret", "")
    monkeypatch.setattr(commands.settings, "trend_sift_db_path", tmp_path / "doctor.db")
    monkeypatch.setattr(llm, "probe", lambda: "json_object")

    assert commands.cmd_doctor(argparse.Namespace()) == 0
    output = capsys.readouterr().out
    assert "仅正式推送需要" in output
    assert "LLM 摘要额度" in output
    assert "全部就绪" in output
