"""Argument parsing for the ``trend-sift`` command."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from . import commands

Command = Callable[[argparse.Namespace], int]


def _source_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> argparse._SubParsersAction[argparse.ArgumentParser]:
    parser = subparsers.add_parser(name, help=help_text)
    return parser.add_subparsers(dest=f"{name}_command", required=True)


def _dated_command(
    group: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    handler: Command,
    date_help: str,
) -> argparse.ArgumentParser:
    parser = group.add_parser(name, help=help_text)
    parser.set_defaults(func=handler)
    parser.add_argument("--date", help=date_help)
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Build and return the public command-line parser."""
    parser = argparse.ArgumentParser(
        prog="trend-sift",
        description="聚合 GitHub Trending 与 Hacker News，并生成中文摘要",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="输出 DEBUG 日志")
    groups = parser.add_subparsers(dest="command", required=True)

    run = groups.add_parser("run", help="运行全部数据源")
    run.set_defaults(func=commands.cmd_run)
    run.add_argument("--github-date", help="GitHub 快照日期 YYYY-MM-DD，默认今天")
    run.add_argument("--hn-date", help="Hacker News UTC 日期，默认按 HN_LAG_DAYS 回溯")
    run.add_argument("--dry-run", action="store_true", help="只预览，不发送飞书消息")
    groups.add_parser("doctor", help="检查配置与外部依赖").set_defaults(func=commands.cmd_doctor)

    github = _source_parser(groups, "github", "操作 GitHub Trending")
    gh_date = "YYYY-MM-DD，默认今天"
    _dated_command(github, "run", "运行完整流水线", commands.cmd_gh_run, gh_date).add_argument(
        "--dry-run", action="store_true", help="只预览，不发送"
    )
    _dated_command(github, "fetch", "抓取榜单并入库", commands.cmd_gh_fetch, gh_date)
    enrich = _dated_command(
        github, "enrich", "补全 topics 和 README", commands.cmd_gh_enrich, gh_date
    )
    enrich.add_argument("--force", action="store_true", help="忽略缓存")
    summarize = _dated_command(
        github, "summarize", "生成中文摘要", commands.cmd_gh_summarize, gh_date
    )
    summarize.add_argument("--force", action="store_true", help="重新生成已有摘要")
    _dated_command(github, "notify", "推送飞书卡片", commands.cmd_gh_notify, gh_date).add_argument(
        "--dry-run", action="store_true", help="只预览，不发送"
    )
    _dated_command(github, "show", "查看某日结果", commands.cmd_gh_show, gh_date)
    history = github.add_parser("history", help="查看仓库时序变化")
    history.set_defaults(func=commands.cmd_gh_history)
    history.add_argument("repo", help="owner/repo")
    reparse = github.add_parser("reparse", help="从归档 HTML 重放解析")
    reparse.set_defaults(func=commands.cmd_gh_reparse)
    reparse.add_argument("date", help="YYYY-MM-DD")

    hacker_news = _source_parser(groups, "hacker-news", "操作 Hacker News")
    hn_date = "YYYY-MM-DD (UTC)，默认按 HN_LAG_DAYS 回溯"
    _dated_command(hacker_news, "run", "运行完整流水线", commands.cmd_hn_run, hn_date).add_argument(
        "--dry-run", action="store_true", help="只预览，不发送"
    )
    _dated_command(hacker_news, "fetch", "抓取高分帖并入库", commands.cmd_hn_fetch, hn_date)
    enrich = _dated_command(hacker_news, "enrich", "抓取原文正文", commands.cmd_hn_enrich, hn_date)
    enrich.add_argument("--force", action="store_true", help="忽略缓存")
    _dated_command(
        hacker_news, "summarize", "生成中文摘要", commands.cmd_hn_summarize, hn_date
    ).add_argument("--force", action="store_true", help="重新生成已有摘要")
    notify = _dated_command(hacker_news, "notify", "推送飞书卡片", commands.cmd_hn_notify, hn_date)
    notify.add_argument("--dry-run", action="store_true", help="只预览，不发送")
    _dated_command(hacker_news, "show", "查看某日结果", commands.cmd_hn_show, hn_date)
    reparse = hacker_news.add_parser("reparse", help="从归档 JSON 重放解析")
    reparse.set_defaults(func=commands.cmd_hn_reparse)
    reparse.add_argument("date", help="YYYY-MM-DD (UTC)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    commands.setup_logging(args.verbose)
    return args.func(args)
