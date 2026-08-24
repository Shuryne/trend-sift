import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from trend_sift.sources.hacker_news.fetcher import ParseError, build_url, parse_feed, target_date
from trend_sift.sources.hacker_news.models import hn_url, target_url


def test_parse_hn_fixture_sorts_and_skips_invalid(fixture_dir: Path) -> None:
    body = (fixture_dir / "hn_feed.json").read_text()
    rows = parse_feed(body, "2026-08-22", "now", top_n=3)

    assert [row.object_id for row in rows] == ["200", "100"]
    assert rows[0].rank == 1
    assert rows[0].target_url == hn_url("200")
    assert rows[1].target_url == "https://example.com/low"


def test_parse_hn_validates_json_shape() -> None:
    with pytest.raises(ParseError, match="合法 JSON"):
        parse_feed("not-json", "2026-08-22", "now")
    with pytest.raises(ParseError, match="hits 数组"):
        parse_feed("{}", "2026-08-22", "now")
    with pytest.raises(ParseError, match="全部解析失败"):
        parse_feed(json.dumps({"hits": [{"title": "missing id"}]}), "2026-08-22", "now")


def test_hn_url_and_day_bounds_are_explicit() -> None:
    url = build_url("2026-08-22", min_points=321)
    query = parse_qs(urlparse(url).query)
    assert "points>=321" in query["numericFilters"][0]
    assert target_date(lag_days=0).count("-") == 2
    assert target_url(None, "42") == "https://news.ycombinator.com/item?id=42"
