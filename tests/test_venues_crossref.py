# tests/test_venues_crossref.py

from datetime import datetime, timezone
from typing import Any, Dict

from pathlib import Path
import pytest
import sys
ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from paper_tracker.config import VenueConfig
from paper_tracker.models import Paper
from paper_tracker.venues import crossref as crossref_mod
from paper_tracker.venues.crossref import CrossrefVenueFetcher


def _make_venue_config() -> VenueConfig:
    """构造一个最小可用的 VenueConfig，用于测试 Crossref fetcher。"""
    # issn 可以直接用 dict，Pydantic 会转成 IssnInfo
    return VenueConfig(
        id="test_venue",
        name="Test Journal",
        fetcher="crossref",
        issn={"print": "1234-5678", "online": "8765-4321"},
    )


class DummyResponse:
    """模拟 requests.Response 的最小实现，只支持 status_code 和 json()."""

    def __init__(self, status_code: int, json_data: Dict[str, Any] | None = None):
        self.status_code = status_code
        self._json_data = json_data

    def json(self) -> Dict[str, Any]:
        if self._json_data is None:
            raise ValueError("No JSON")
        return self._json_data


def test_crossref_fetcher_success(monkeypatch):
    """正常返回 200 且有一条记录时，应该正确解析为 Paper。"""

    venue = _make_venue_config()
    fetcher = CrossrefVenueFetcher(venue)

    # 构造一条 Crossref item
    item = {
        "title": ["Awesome Method for Single-cell Analysis"],
        "DOI": "10.1234/ABC.DEF",
        "container-title": ["Real Journal Name"],
        "URL": "https://doi.org/10.1234/ABC.DEF",
        "published-online": {"date-parts": [[2020, 5, 1]]},
        "author": [
            {"given": "Alice", "family": "Smith"},
            {"given": "Bob", "family": "Jones"},
        ],
    }

    json_data = {
        "status": "ok",
        "message": {
            "items-per-page": 1,
            "items": [item],
        },
    }

    def fake_get(url, params=None, headers=None, timeout=None):
        # 简单检查下 URL 是否为 Crossref 的 works 端点
        assert "crossref.org/works" in url
        return DummyResponse(status_code=200, json_data=json_data)

    # 只 patch crossref_mod.requests.get，保留原始 requests.RequestException 等类型
    monkeypatch.setattr(crossref_mod.requests, "get", fake_get)

    papers = fetcher.fetch_recent(days_back=3)
    assert len(papers) == 1

    p: Paper = papers[0]
    assert p.title == "Awesome Method for Single-cell Analysis"
    assert p.doi == "10.1234/ABC.DEF"
    assert p.journal == "Real Journal Name"
    assert p.venue_id == venue.id
    assert p.url == "https://doi.org/10.1234/ABC.DEF"

    # 作者与第一作者
    assert p.authors == ["Alice Smith", "Bob Jones"]
    assert p.first_author == "Alice Smith"

    # 发表时间应正确解析为 2020-05-01（UTC）
    assert isinstance(p.published_at, datetime)
    assert p.published_at.year == 2020
    assert p.published_at.month == 5
    assert p.published_at.day == 1
    assert p.published_at.tzinfo == timezone.utc


def test_crossref_fetcher_fallback_journal_name(monkeypatch):
    """当 container-title 缺失时，应回退使用 VenueConfig.name 作为期刊名。"""

    venue = _make_venue_config()
    fetcher = CrossrefVenueFetcher(venue)

    item = {
        "title": ["Title Without Container"],
        "DOI": "10.1111/NO.CONTAINER",
        "URL": "https://doi.org/10.1111/NO.CONTAINER",
        # container-title 缺失
        "published-print": {"date-parts": [[2021, 1, 15]]},
        "author": [],
    }

    json_data = {
        "status": "ok",
        "message": {
            "items": [item],
        },
    }

    def fake_get(url, params=None, headers=None, timeout=None):
        return DummyResponse(status_code=200, json_data=json_data)

    monkeypatch.setattr(crossref_mod.requests, "get", fake_get)

    papers = fetcher.fetch_recent(days_back=7)
    assert len(papers) == 1
    p = papers[0]

    assert p.journal == venue.name  # 回退到配置里的 name
    assert p.authors == []
    assert p.first_author is None


def test_crossref_fetcher_non_200_returns_empty(monkeypatch):
    """Crossref 返回非 200 状态码时，应返回空列表。"""

    venue = _make_venue_config()
    fetcher = CrossrefVenueFetcher(venue)

    def fake_get(url, params=None, headers=None, timeout=None):
        return DummyResponse(status_code=500, json_data=None)

    monkeypatch.setattr(crossref_mod.requests, "get", fake_get)

    papers = fetcher.fetch_recent(days_back=3)
    assert papers == []


def test_crossref_fetcher_request_exception(monkeypatch):
    """请求过程中发生 RequestException 时，应返回空列表。"""

    venue = _make_venue_config()
    fetcher = CrossrefVenueFetcher(venue)

    def fake_get(url, params=None, headers=None, timeout=None):
        raise crossref_mod.requests.RequestException("boom")

    monkeypatch.setattr(crossref_mod.requests, "get", fake_get)

    papers = fetcher.fetch_recent(days_back=3)
    assert papers == []


def test_crossref_fetcher_bad_json(monkeypatch):
    """Crossref 响应无法解析为 JSON 时，应返回空列表。"""

    venue = _make_venue_config()
    fetcher = CrossrefVenueFetcher(venue)

    class BadJsonResponse(DummyResponse):
        def json(self):
            raise ValueError("invalid json")

    def fake_get(url, params=None, headers=None, timeout=None):
        return BadJsonResponse(status_code=200, json_data=None)

    monkeypatch.setattr(crossref_mod.requests, "get", fake_get)

    papers = fetcher.fetch_recent(days_back=3)
    assert papers == []
