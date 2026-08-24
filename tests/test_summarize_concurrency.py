import threading
from pathlib import Path

import pytest

from trend_sift.core.schema import init_db
from trend_sift.core.store import connect
from trend_sift.sources.github import summarize


def test_github_batch_skips_cache_runs_concurrently_and_saves_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "summaries.db"
    monkeypatch.setattr(summarize.settings, "trend_sift_db_path", db_path)
    monkeypatch.setattr(summarize.settings, "llm_summary_concurrency", 3)
    init_db(db_path)

    repos: list[tuple[str, str | None]] = [
        (f"owner/repo-{index}", f"description-{index}") for index in range(4)
    ]
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO gh_repos (full_name, topics, readme_head, enriched_at) "
            "VALUES (?, '[]', ?, '2026-08-24T00:00:00+08:00')",
            [(name, f"readme-{index}") for index, (name, _) in enumerate(repos)],
        )
        conn.execute(
            "INSERT INTO gh_summaries "
            "(full_name, summary_zh, prompt_version, model, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (repos[0][0], "cached", summarize.PROMPT_VERSION, "model", "2026-08-24"),
        )

    lock = threading.Lock()
    active = 0
    peak = 0
    called: list[str] = []
    workers_ready = threading.Barrier(3)

    def fake_summarize_one(
        full_name: str,
        description: str | None,
        topics: list[str],
        readme: str | None,
    ) -> summarize.Summary:
        del description, topics, readme
        nonlocal active, peak
        with lock:
            called.append(full_name)
            active += 1
            peak = max(peak, active)
        workers_ready.wait(timeout=1)
        with lock:
            active -= 1
        return summarize.Summary(full_name, f"summary-{full_name}", "model")

    monkeypatch.setattr(summarize, "summarize_one", fake_summarize_one)

    results = summarize.summarize_batch(repos)

    assert [result.full_name for result in results] == [name for name, _ in repos[1:]]
    assert set(called) == {name for name, _ in repos[1:]}
    assert peak == 3
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT full_name, summary_zh FROM gh_summaries ORDER BY full_name"
        ).fetchall()
    assert [(row["full_name"], row["summary_zh"]) for row in rows] == [
        (repos[0][0], "cached"),
        *[(name, f"summary-{name}") for name, _ in repos[1:]],
    ]
