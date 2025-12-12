# tests/test_config.py

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from paper_tracker.config import (
    KeywordsConfig,
    SourcesConfig,
    VenuesConfig,
    load_keywords,
    load_sources,
    load_venues,
)

import pytest

def test_load_keywords_default():
    """默认路径下的 keywords.yml 能成功加载并返回正确类型。"""
    cfg = load_keywords()
    assert isinstance(cfg, KeywordsConfig)
    assert isinstance(cfg.include_keywords_1, list)
    assert isinstance(cfg.include_keywords_2, list)
    assert isinstance(cfg.exclude_keywords, list)


def test_load_sources_default():
    """默认路径下的 sources.yml 能成功加载并返回正确类型。"""
    cfg = load_sources()
    assert isinstance(cfg, SourcesConfig)
    # 基本结构检查
    for src in cfg.sources:
        assert src.id  # 非空字符串
        assert src.type  # 非空字符串
        assert isinstance(src.enabled, bool)
        assert isinstance(src.search_by, list)
        assert isinstance(src.provides, list)
        assert isinstance(src.priority, dict)



def test_load_venues_default():
    """默认路径下的 venues.yml 能成功加载并返回正确类型。"""
    cfg = load_venues()
    assert isinstance(cfg, VenuesConfig)
    # 至少应该有一个期刊配置
    assert isinstance(cfg.venues, list)
    assert len(cfg.venues) >= 1

    for v in cfg.venues:
        assert v.id
        assert v.name
        assert v.fetcher
        # fetcher_params.days_back 应为正整数
        assert v.fetcher_params.days_back > 0


def test_sources_enabled_sources_helper():
    """enabled_sources() 只返回 enabled == True 的 source。"""
    cfg = load_sources()
    enabled = cfg.enabled_sources()
    for src in enabled:
        assert src.enabled is True


def test_venues_by_id_helper():
    """by_id() 能正常构建 id -> VenueConfig 的映射。"""
    cfg = load_venues()
    mapping = cfg.by_id()
    # 映射中键集合应覆盖所有 venue.id
    ids_from_venues = {v.id for v in cfg.venues}
    assert set(mapping.keys()) == ids_from_venues
    for vid, venue in mapping.items():
        assert vid == venue.id


def test_load_keywords_invalid_top_level(tmp_path: Path):
    """当 YAML 顶层不是 mapping 时，应抛出 ValueError。"""
    bad_file = tmp_path / "bad_keywords.yml"
    # 顶层为 list，而不是 dict
    bad_file.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_keywords(path=bad_file)


def test_load_sources_invalid_top_level(tmp_path: Path):
    """sources.yml 顶层不是 mapping 时，应抛出 ValueError。"""
    bad_file = tmp_path / "bad_sources.yml"
    bad_file.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_sources(path=bad_file)


def test_load_venues_invalid_top_level(tmp_path: Path):
    """venues.yml 顶层不是 mapping 时，应抛出 ValueError。"""
    bad_file = tmp_path / "bad_venues.yml"
    bad_file.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_venues(path=bad_file)


def test_missing_config_file_raises(tmp_path: Path):
    """显式指定的配置文件不存在时，应抛出 FileNotFoundError。"""
    missing = tmp_path / "missing.yml"
    with pytest.raises(FileNotFoundError):
        load_keywords(path=missing)
