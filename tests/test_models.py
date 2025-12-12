# tests/test_models.py

from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
import sys
ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
    
from paper_tracker.models import Paper, ScoringResult


# ===== ScoringResult 测试 =====

def test_scoring_result_basic():
    sr = ScoringResult(
        total_score=10.5,
        components={"keyword_title": 6.0, "venue": 4.5},
        tags=["GNN", "single-cell"],
        version="v1",
    )

    assert sr.total_score == 10.5
    assert sr.components["keyword_title"] == 6.0
    assert "GNN" in sr.tags
    assert sr.version == "v1"


def test_scoring_result_to_dict_from_dict_roundtrip():
    original = ScoringResult(
        total_score=15.0,
        components={"keyword_title": 5.0, "keyword_abs": 7.0, "venue": 3.0},
        tags=["GNN", "multi-omics"],
        version="v2",
    )

    data = original.to_dict()
    restored = ScoringResult.from_dict(data)

    assert isinstance(data, dict)
    assert restored.total_score == original.total_score
    assert restored.components == original.components
    assert restored.tags == original.tags
    assert restored.version == original.version


def test_scoring_result_from_dict_with_missing_fields():
    data = {
        "total_score": 3.14,
        # components / tags / version 缺失
    }
    sr = ScoringResult.from_dict(data)
    assert sr.total_score == pytest.approx(3.14)
    assert sr.components == {}
    assert sr.tags == []
    assert sr.version == "v1"  # 默认版本


def test_scoring_result_from_dict_ignores_unknown_fields():
    data = {
        "total_score": 1.0,
        "components": {"x": 1.0},
        "tags": ["a"],
        "version": "v1",
        "extra_field": "should_be_ignored",
    }
    sr = ScoringResult.from_dict(data)
    assert sr.total_score == 1.0
    assert sr.components == {"x": 1.0}
    assert sr.tags == ["a"]
    assert sr.version == "v1"
    # 不应该把 extra_field 放进对象属性
    assert not hasattr(sr, "extra_field")


# ===== Paper 测试 =====

def test_paper_basic_creation_defaults():
    p = Paper(
        id="test-id",
        title="Test Title",
        doi="10.1234/test",
        journal="Test Journal",
        venue_id="test_venue",
    )

    assert p.id == "test-id"
    assert p.title == "Test Title"
    assert p.doi == "10.1234/test"
    assert p.journal == "Test Journal"
    assert p.venue_id == "test_venue"

    # 默认字段
    assert p.url is None
    assert p.pdf_url is None
    # assert p.short_abstract is None
    assert p.score_summary is None
    assert p.score_version is None

    assert p.abstract_source is None
    # assert p.oa_status is None
    # assert p.oa_source is None
    assert p.metadata_sources == []
    assert p.enrichment_version is None

    # authors 默认是空列表
    assert isinstance(p.authors, list)
    assert p.authors == []

    # first_author 默认 None
    assert p.first_author is None

    # 时间戳字段默认存在且为 aware datetime
    assert isinstance(p.created_at, datetime)
    assert p.created_at.tzinfo is not None
    assert isinstance(p.updated_at, datetime)
    assert p.updated_at.tzinfo is not None


def test_paper_authors_and_first_author():
    authors = ["Alice", "Bob", "Carol"]
    p = Paper(
        id="test-id",
        title="Test Title",
        authors=authors,
        first_author=authors[0],
    )

    assert p.authors == authors
    assert p.first_author == "Alice"


def test_paper_touch_updated_changes_timestamp():
    p = Paper(id="test-id", title="Test Title")
    old_updated = p.updated_at
    # 确保时间间隔可观测
    p.touch_updated()
    new_updated = p.updated_at

    assert isinstance(new_updated, datetime)
    assert new_updated.tzinfo is not None
    assert new_updated >= old_updated


def test_paper_to_dict_serializes_datetimes():
    published_at = datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc)
    created_at = datetime(2020, 1, 2, 12, 0, tzinfo=timezone.utc)
    updated_at = datetime(2020, 1, 3, 12, 0, tzinfo=timezone.utc)

    p = Paper(
        id="test-id",
        title="Test Title",
        published_at=published_at,
        created_at=created_at,
        updated_at=updated_at,
    )

    data = p.to_dict()
    assert isinstance(data, dict)
    assert data["id"] == "test-id"
    assert data["title"] == "Test Title"

    # datetime 被序列化为 ISO 格式字符串
    assert data["published_at"] == published_at.isoformat()
    assert data["created_at"] == created_at.isoformat()
    assert data["updated_at"] == updated_at.isoformat()


def test_paper_from_dict_roundtrip():
    published_at = datetime(2021, 5, 6, 8, 30, tzinfo=timezone.utc)
    created_at = datetime(2021, 5, 7, 9, 30, tzinfo=timezone.utc)
    updated_at = datetime(2021, 5, 8, 10, 30, tzinfo=timezone.utc)

    original = Paper(
        id="test-id",
        title="Roundtrip Title",
        doi="10.9999/rt",
        journal="RT Journal",
        venue_id="rt_venue",
        url="https://example.com/paper",
        pdf_url="https://example.com/paper.pdf",
        short_abstract="Short abstract here.",
        score_summary={"total_score": 42.0, "tags": ["GNN"]},
        score_version="v1",
        authors=["Author A", "Author B"],
        first_author="Author A",
        published_at=published_at,
        created_at=created_at,
        updated_at=updated_at,
    )

    data = original.to_dict()
    restored = Paper.from_dict(data)

    assert restored.id == original.id
    assert restored.title == original.title
    assert restored.doi == original.doi
    assert restored.journal == original.journal
    assert restored.venue_id == original.venue_id
    assert restored.url == original.url
    assert restored.pdf_url == original.pdf_url
    assert restored.short_abstract == original.short_abstract
    assert restored.score_summary == original.score_summary
    assert restored.score_version == original.score_version
    assert restored.authors == original.authors
    assert restored.first_author == original.first_author

    # datetime 字段 roundtrip 成功（isoformat -> datetime）
    assert restored.published_at == published_at
    assert restored.created_at == created_at
    assert restored.updated_at == updated_at


def test_paper_from_dict_missing_datetimes():
    """当 dict 中缺失 datetime 字段时，published_at 为 None，created/updated 使用当前时间。"""
    data = {
        "id": "test-id",
        "title": "No datetime",
    }

    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    p = Paper.from_dict(data)
    after = datetime.now(timezone.utc) + timedelta(seconds=5)

    assert p.id == "test-id"
    assert p.title == "No datetime"

    # published_at 缺失 -> None
    assert p.published_at is None

    # created_at / updated_at 在 before-after 窗口内（大致检查）
    assert isinstance(p.created_at, datetime)
    assert p.created_at.tzinfo is not None
    assert before <= p.created_at <= after

    assert isinstance(p.updated_at, datetime)
    assert p.updated_at.tzinfo is not None
    assert before <= p.updated_at <= after


def test_paper_from_dict_ignores_unknown_fields():
    data = {
        "id": "test-id",
        "title": "Has extra fields",
        "unknown_field": 123,
    }

    p = Paper.from_dict(data)
    assert p.id == "test-id"
    assert p.title == "Has extra fields"
    # 未知字段不应成为属性
    assert not hasattr(p, "unknown_field")
