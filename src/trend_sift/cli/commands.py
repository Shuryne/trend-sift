"""CLI command handlers and human-readable terminal output."""

import argparse
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Any
from urllib.parse import urlparse

from ..core.config import settings
from ..core.schema import init_db
from ..core.store import connect, today_str
from ..notifications.feishu import fmt_num, payload_kb
from ..sources.github import pipeline as gh_pipeline
from ..sources.github.config import period_list
from ..sources.github.notify import PERIOD_META, PERIOD_ORDER, items_by_period
from ..sources.github.notify import build_card as gh_build_card
from ..sources.github.store import days_on_board, distinct_repos_on
from ..sources.github.summarize import PROMPT_VERSION as GH_PROMPT_VERSION
from ..sources.hacker_news import pipeline as hn_pipeline
from ..sources.hacker_news.fetcher import target_date as hn_target_date
from ..sources.hacker_news.notify import build_card as hn_build_card
from ..sources.hacker_news.notify import items_for
from ..sources.hacker_news.store import stories_on
from ..sources.hacker_news.summarize import PROMPT_VERSION as HN_PROMPT_VERSION


def setup_logging(verbose: bool = False) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)

    # cron 场景下 stdout 会丢，日志必须落文件
    file_handler = RotatingFileHandler(
        settings.log_dir / "trend-sift.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[console, file_handler],
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.DEBUG if verbose else logging.WARNING)


# ---------------------------------------------------------------- GitHub


def cmd_gh_fetch(args: argparse.Namespace) -> int:
    stats = gh_pipeline.run_fetch(period_list(), args.date)
    print(f"\n快照日期 {stats['snapshot_date']}  状态 {stats['status']}")
    for period, s in stats["periods"].items():
        if "error" in s:
            print(f"  {period:<8} 失败: {s['error']}")
        else:
            print(f"  {period:<8} 解析 {s['parsed']:>3} 条，新增 {s['inserted']:>3} 条")
    print(f"  合计解析 {stats['fetched']} 条，去重后首次上榜 {stats['new_repos']} 个仓库")
    return 0 if stats["status"] == "ok" else 1


def cmd_gh_enrich(args: argparse.Namespace) -> int:
    s = gh_pipeline.run_enrich(args.date, force=args.force)
    print(
        f"\n富化：新增 {s['enriched']}，命中缓存 {s['cached']}，"
        f"失败 {s['failed']}，因限流跳过 {s['skipped_rate_limit']}"
    )
    if s["skipped_rate_limit"]:
        print("  提示：配置 GITHUB_TOKEN 可把限额从 60 次/小时提到 5000 次/小时")
    return 0


def cmd_gh_summarize(args: argparse.Namespace) -> int:
    s = gh_pipeline.run_summarize(args.date, force=args.force)
    print(
        f"\n摘要（prompt 版本 {s['prompt_version']}）："
        f"新生成 {s['ok']}，失败 {s['failed']}，命中缓存 {s['skipped_cached']}"
    )
    return 0


def cmd_gh_notify(args: argparse.Namespace) -> int:
    s = gh_pipeline.run_notify(args.date, dry_run=args.dry_run)
    if args.dry_run:
        print("\n[dry-run] 以下内容不会实际发送")
        _gh_preview(args.date)
    else:
        detail = "，".join(f"{p} {n} 个" for p, n in s["by_period"].items())
        print(f"\n已发送 {s['cards']} 张卡片（{detail}）")
    return 0


def cmd_gh_run(args: argparse.Namespace) -> int:
    stats = gh_pipeline.run_all(period_list(), args.date, dry_run=args.dry_run)
    _print_pipeline("GitHub Trending", stats)
    if args.dry_run:
        _gh_preview(args.date)
    return 0 if stats["status"] == "ok" else 1


def _gh_preview(date: str | None) -> None:
    """打印将要推送的内容 —— 结构与实际卡片一致，含折叠分界线。"""
    snapshot_date = date or today_str()
    grouped = items_by_period(snapshot_date, GH_PROMPT_VERSION)
    if not grouped:
        print("  （没有待推送项目）")
        return

    n = settings.expanded_per_section
    sizes: list[str] = []

    for period in PERIOD_ORDER:
        items = grouped.get(period)
        if not items:
            continue
        meta = PERIOD_META[period]
        new_count = sum(1 for i in items if i.is_new)
        print(f"\n╭─ {meta.title} " + "─" * (72 - len(meta.title)))
        for i, it in enumerate(items, 1):
            if i == n + 1:
                print(f"│  ▸ 展开剩余 {len(items) - n} 个 " + "┈" * 38)
            growth = f"{meta.growth_label} +{fmt_num(it.stars_period)}" if it.stars_period else ""
            tags = [f"⭐{fmt_num(it.stars)}", growth, it.language or ""]
            if it.days > 1:
                tags.append(f"在榜{it.days}天")
            fold = "  " if i > n else ""  # 折叠区内的条目缩进，和展开区区分
            print(f"│ {fold}{i:>2}. {it.full_name}{' 🆕' if it.is_new else ''}")
            print(f"│ {fold}     {it.summary_zh or it.description or '（无描述）'}")
            print(f"│ {fold}     {'  ·  '.join(t for t in tags if t)}")
        payload = {
            "msg_type": "interactive",
            "card": gh_build_card(items, period, snapshot_date),
        }
        kb = payload_kb(payload)
        sizes.append(f"{period} {kb:.1f}KB")
        print(
            f"╰─ 共 {len(items)} 个项目，展开 {min(n, len(items))} 个"
            f"，🆕 {new_count} 个首次上榜  ·  {kb:.1f} KB"
        )

    total = sum(len(v) for v in grouped.values())
    print(
        f"\n将发送 {len(grouped)} 张卡片，合计 {total} 条目（含跨榜重复）"
        f"；体积 {'，'.join(sizes)}，单张上限 20 KB"
    )


def cmd_gh_show(args: argparse.Namespace) -> int:
    date = args.date or today_str()
    init_db()
    with connect() as conn:
        rows = distinct_repos_on(conn, date)
        if not rows:
            print(f"{date} 没有数据，先跑一次 fetch")
            return 1
        summaries = {
            r["full_name"]: r["summary_zh"]
            for r in conn.execute(
                "SELECT full_name, summary_zh FROM gh_summaries WHERE prompt_version = ?",
                (GH_PROMPT_VERSION,),
            )
        }
        notified = {r["full_name"] for r in conn.execute("SELECT full_name FROM gh_notifications")}

        have = sum(1 for r in rows if summaries.get(r["full_name"]))
        print(f"\n{date}  去重后 {len(rows)} 个仓库，其中 {have} 个已有摘要\n")
        for r in rows:
            summary = summaries.get(r["full_name"])
            print(
                f"{r['full_name'][:38]:<40}"
                f"{(r['language'] or '-')[:11]:<13}"
                f"{r['stars'] or 0:>9,}"
                f"{r['stars_period'] or 0:>8,}"
                f"{days_on_board(conn, r['full_name']):>5} 天"
                f"  {'√' if r['full_name'] in notified else ' '}"
            )
            print(f"    {summary or '（无摘要）'}")
    return 0


def cmd_gh_history(args: argparse.Namespace) -> int:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT snapshot_date, period, rank, stars, stars_period
            FROM gh_snapshots WHERE full_name = ?
            ORDER BY snapshot_date, period
            """,
            (args.repo,),
        ).fetchall()
        if not rows:
            print(f"库里没有 {args.repo} 的记录")
            return 1
        print(f"\n{args.repo} 的历史轨迹\n")
        print(f"{'日期':<13}{'榜单':<10}{'名次':>5}{'总星':>10}{'增量':>9}")
        print("─" * 50)
        for r in rows:
            print(
                f"{r['snapshot_date']:<13}{r['period']:<10}{r['rank']:>5}"
                f"{r['stars'] or 0:>10,}{r['stars_period'] or 0:>9,}"
            )
    return 0


def cmd_gh_reparse(args: argparse.Namespace) -> int:
    stats = gh_pipeline.run_reparse(args.date, period_list())
    for period, s in stats["periods"].items():
        if "error" in s:
            print(f"  {period:<8} {s['error']}")
        else:
            print(f"  {period:<8} 解析 {s['parsed']:>3} 条，新增 {s['inserted']:>3} 条")
    return 0


# ---------------------------------------------------------------- Hacker News


def cmd_hn_fetch(args: argparse.Namespace) -> int:
    stats = hn_pipeline.run_fetch(args.date)
    print(f"\nHN {stats['snapshot_date']} (UTC)  状态 {stats['status']}")
    if stats["errors"]:
        print(f"  失败: {'; '.join(stats['errors'])}")
    else:
        print(
            f"  解析 {stats['fetched']} 条，新增 {stats['inserted']} 条，"
            f"首次上榜 {stats['new_stories']} 条"
        )
    return 0 if stats["status"] == "ok" else 1


def cmd_hn_enrich(args: argparse.Namespace) -> int:
    s = hn_pipeline.run_enrich(args.date, force=args.force)
    print(
        f"\n富化：抓到正文 {s['enriched']}，纯文本帖 {s['no_url']}，"
        f"命中缓存 {s['cached']}，未抓到正文 {s['failed']}"
    )
    if s["failed"]:
        print("  提示：抓不到正文是常态（付费墙 / JS 渲染 / 反爬），摘要会退回只用标题")
    return 0


def cmd_hn_summarize(args: argparse.Namespace) -> int:
    s = hn_pipeline.run_summarize(args.date, force=args.force)
    print(
        f"\n摘要（prompt 版本 {s['prompt_version']}）："
        f"新生成 {s['ok']}，失败 {s['failed']}，命中缓存 {s['skipped_cached']}"
    )
    return 0


def cmd_hn_notify(args: argparse.Namespace) -> int:
    s = hn_pipeline.run_notify(args.date, dry_run=args.dry_run)
    if args.dry_run:
        print("\n[dry-run] 以下内容不会实际发送")
        _hn_preview(args.date)
    else:
        print(f"\n已发送 {s['cards']} 张卡片（{s['items']} 条）")
    return 0


def cmd_hn_run(args: argparse.Namespace) -> int:
    stats = hn_pipeline.run_all(args.date, dry_run=args.dry_run)
    _print_pipeline("Hacker News", stats)
    if args.dry_run:
        _hn_preview(args.date or stats["snapshot_date"])
    return 0 if stats["status"] == "ok" else 1


def _hn_preview(date: str | None) -> None:
    day = date or hn_target_date()
    items = items_for(day, HN_PROMPT_VERSION)
    if not items:
        print("  （没有待推送条目）")
        return

    n = settings.expanded_per_section
    new_count = sum(1 for i in items if i.is_new)
    print(f"\n╭─ Hacker News 热帖 {day} (UTC) " + "─" * 42)
    for i, it in enumerate(items, 1):
        if i == n + 1:
            print(f"│  ▸ 展开剩余 {len(items) - n} 条 " + "┈" * 38)
        fold = "  " if i > n else ""
        tags = [f"▲{fmt_num(it.points)}", f"{it.num_comments} 评论"]
        if it.author:
            tags.append(f"@{it.author}")
        if it.url is None:
            tags.append("文本帖")
        print(f"│ {fold}{i:>2}. {it.title[:66]}{' 🆕' if it.is_new else ''}")
        print(f"│ {fold}     {it.summary_zh or '（无摘要）'}")
        print(f"│ {fold}     {'  ·  '.join(tags)}")

    kb = payload_kb({"msg_type": "interactive", "card": hn_build_card(items, day)})
    print(
        f"╰─ 共 {len(items)} 条，展开 {min(n, len(items))} 条"
        f"，🆕 {new_count} 条首次上榜  ·  {kb:.1f} KB"
    )


def cmd_hn_show(args: argparse.Namespace) -> int:
    day = args.date or hn_target_date()
    init_db()
    with connect() as conn:
        rows = stories_on(conn, day)
        if not rows:
            print(f"{day} 没有 HN 数据，先跑一次 fetch")
            return 1
        summaries = {
            r["object_id"]: r["summary_zh"]
            for r in conn.execute(
                "SELECT object_id, summary_zh FROM hn_summaries WHERE prompt_version = ?",
                (HN_PROMPT_VERSION,),
            )
        }
        notified = {r["object_id"] for r in conn.execute("SELECT object_id FROM hn_notifications")}

        have = sum(1 for r in rows if summaries.get(r["object_id"]))
        print(f"\nHN {day} (UTC)  {len(rows)} 条，其中 {have} 条已有摘要\n")
        for r in rows:
            mark = "√" if r["object_id"] in notified else " "
            kind = "  [文本帖]" if not r["url"] else ""
            print(
                f"{r['rank']:>3}. ▲{r['points'] or 0:<6} 💬{r['num_comments'] or 0:<5}"
                f" {mark}  {r['title'][:60]}{kind}"
            )
            print(f"      {summaries.get(r['object_id']) or '（无摘要）'}")
    return 0


def cmd_hn_reparse(args: argparse.Namespace) -> int:
    stats = hn_pipeline.run_reparse(args.date)
    if "error" in stats:
        print(f"  {stats['error']}")
        return 1
    print(f"  解析 {stats['parsed']} 条，新增 {stats['inserted']} 条")
    return 0


# ---------------------------------------------------------------- 顶层


def _print_pipeline(name: str, stats: dict[str, Any]) -> None:
    bar = "=" * 60
    print(f"\n{bar}\n{name} {stats['snapshot_date']}  状态 {stats['status']}\n{bar}")
    for stage in ("fetch", "enrich", "summarize", "notify"):
        s = stats.get(stage, {})
        if "error" in s:
            print(f"  {stage:<10} 失败: {s['error']}")
        else:
            print(f"  {stage:<10} {json.dumps(s, ensure_ascii=False)[:110]}")
    if stats["errors"]:
        print(f"\n错误：{'; '.join(stats['errors'])}")


def cmd_run(args: argparse.Namespace) -> int:
    """两条流水线都跑。一条挂了不影响另一条 —— 它们没有任何共享状态。"""
    gh = gh_pipeline.run_all(period_list(), args.github_date, dry_run=args.dry_run)
    _print_pipeline("GitHub Trending", gh)
    if args.dry_run:
        _gh_preview(args.github_date)

    hn = hn_pipeline.run_all(args.hn_date, dry_run=args.dry_run)
    _print_pipeline("Hacker News", hn)
    if args.dry_run:
        _hn_preview(hn["snapshot_date"])

    return 0 if gh["status"] == "ok" and hn["status"] == "ok" else 1


def cmd_doctor(_: argparse.Namespace) -> int:
    """自检：确认配置和外部依赖都就绪。密钥只显示是否填写，不打印内容。"""
    ok = True
    print("\n配置检查")
    print("─" * 60)

    def line(name: str, good: bool, detail: str) -> None:
        """必填项：不满足就把整体判为不就绪。"""
        nonlocal ok
        print(f"  {'✓' if good else '✗'} {name:<22} {detail}")
        if not good:
            ok = False

    def note(mark: str, name: str, detail: str) -> None:
        """选填项：只陈述状态，不影响整体结论。"""
        print(f"  {mark} {name:<22} {detail}")

    line(
        "LLM_BASE_URL",
        bool(settings.llm_base_url),
        urlparse(settings.llm_base_url).netloc or "未配置",
    )
    line(
        "LLM_API_KEY",
        bool(settings.llm_api_key),
        "已填写" if settings.llm_api_key else "未配置",
    )
    line("LLM_MODEL", bool(settings.llm_model), settings.llm_model or "未配置")
    note("·", "LLM 输出格式", settings.llm_response_format)
    note(
        "·",
        "LLM 摘要额度",
        f"{settings.llm_summary_initial_tokens} → {settings.llm_summary_max_tokens} tokens",
    )
    note(
        "✓" if settings.github_token else "!",
        "GITHUB_TOKEN",
        "已填写" if settings.github_token else "未配置（匿名限流 60 次/小时，富化可能中途降级）",
    )
    note(
        "✓" if settings.feishu_webhook_url else "!",
        "FEISHU_WEBHOOK_URL",
        urlparse(settings.feishu_webhook_url).netloc
        if settings.feishu_webhook_url
        else "未配置（仅正式推送需要，dry-run 不受影响）",
    )
    note(
        "·",
        "FEISHU_SECRET",
        "已填写（启用签名校验）" if settings.feishu_secret else "未配置（未启用签名校验）",
    )
    note(
        "·",
        "HN 取数窗口",
        f"抓 {settings.hn_lag_days} 天前（{hn_target_date()} UTC）"
        f"，≥{settings.hn_min_points} 分，取前 {settings.hn_top_n} 条",
    )

    print("\n外部依赖")
    print("─" * 60)
    try:
        from ..core.llm import probe

        line("LLM 连通性", True, f"可用，结构化输出模式：{probe()}")
    except Exception as exc:  # noqa: BLE001 — 自检要报告任何失败，不能自己挂掉
        line("LLM 连通性", False, str(exc)[:70])

    try:
        init_db()
        with connect() as conn:
            gh_n = conn.execute("SELECT COUNT(*) c FROM gh_snapshots").fetchone()["c"]
            gh_d = conn.execute(
                "SELECT COUNT(DISTINCT snapshot_date) c FROM gh_snapshots"
            ).fetchone()["c"]
            hn_n = conn.execute("SELECT COUNT(*) c FROM hn_snapshots").fetchone()["c"]
            hn_d = conn.execute(
                "SELECT COUNT(DISTINCT snapshot_date) c FROM hn_snapshots"
            ).fetchone()["c"]
        line(
            "数据库",
            True,
            f"{settings.db_path.name}：GitHub {gh_n} 条/{gh_d} 天，HN {hn_n} 条/{hn_d} 天",
        )
    except Exception as exc:  # noqa: BLE001 — 同上
        line("数据库", False, str(exc)[:70])

    print(f"\n{'全部就绪' if ok else '存在问题，见上方 ✗'}\n")
    return 0 if ok else 1
