from pathlib import Path

import pytest

from trend_sift.sources.github.parser import ParseError, parse_trending


def test_parse_github_fixture(fixture_dir: Path) -> None:
    html = (fixture_dir / "github_trending.html").read_text()
    rows = parse_trending(html, "2026-08-24", "daily", "2026-08-24T09:00:00+08:00")

    assert [row.full_name for row in rows] == ["openai/codex", "astral-sh/uv"]
    assert rows[0].rank == 1
    assert rows[0].stars == 12345
    assert rows[0].forks == 678
    assert rows[0].stars_period == 1234
    assert rows[1].forks is None


def test_parse_github_rejects_error_page() -> None:
    with pytest.raises(ParseError, match="article.Box-row"):
        parse_trending("<html>rate limited</html>", "2026-08-24", "daily", "now")


def test_parse_github_rejects_all_invalid_articles() -> None:
    html = '<article class="Box-row"><h2><a href="/invalid">bad</a></h2></article>'
    with pytest.raises(ParseError, match="全部解析失败"):
        parse_trending(html, "2026-08-24", "weekly", "now")
