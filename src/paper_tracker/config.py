# src/paper_tracker/config.py
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, ValidationError, ConfigDict



# ===== 路径设置 =====

# 假设目录结构为: <repo_root>/src/paper_tracker/config.py
# repo_root = .../../..
REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


# ====== Keywords 配置 ======

class KeywordsConfig(BaseModel):
    """
    对应 keywords.yml 的配置结构。

    兼容目前已有结构：
    - include_keywords: List[str]
    - bio_keywords: List[str]
    - exclude_keywords: List[str]

    后续如需扩展，可在此处添加字段，并允许 extra。
    """
    include_keywords_1: List[str] = Field(default_factory=list)
    include_keywords_2: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


# ====== Sources 配置 ======

class SourceConfig(BaseModel):
    id: str
    type: str
    enabled: bool = True
    base_url: Optional[str] = None
    server: Optional[str] = None
    search_by: List[str] = Field(default_factory=list)

    # 新增：能力声明与优先级（阶段3会用到）
    provides: List[str] = Field(default_factory=list)   # ["metadata","abstract","pdf"]
    priority: Dict[str, int] = Field(default_factory=dict)  # {"abstract":80, "pdf":90}

    model_config = ConfigDict(extra="allow")


class SourcesConfig(BaseModel):
    """
    顶层 sources 配置。
    示例:
    sources:
      - id: arxiv
        ...
      - id: biorxiv
        ...
    """
    sources: List[SourceConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    def enabled_sources(self) -> List[SourceConfig]:
        return [s for s in self.sources if s.enabled]


# ====== Venues 配置 ======

class IssnInfo(BaseModel):
    """
    期刊的 ISSN 信息，支持 print / online。
    示例:
      issn:
        print: "0305-1048"
        online: "1362-4962"
    """
    print: Optional[str] = None
    online: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class VenueFetcherParams(BaseModel):
    """
    期刊抓取器的参数，当前主要使用 days_back。
    示例:
      fetcher_params:
        days_back: 3
    """
    days_back: int = 3
    query: Optional[str] = None  # 预留字段

    model_config = ConfigDict(extra="allow")


class VenueConfig(BaseModel):
    """
    单个期刊配置项，对应 venues.yml 中的每个元素。
    示例:
      - id: nar
        name: "Nucleic Acids Research"
        publisher: "Oxford University Press"
        issn:
          print: "0305-1048"
          online: "1362-4962"
        website: "https://academic.oup.com/nar"
        tier: "A"
        topics: ["bioinformatics", "databases", "web-servers", "methods", "tools"]
        notes: "..."
        fetcher: "crossref"
        fetcher_params:
          days_back: 3
    """
    id: str
    name: str
    publisher: Optional[str] = None
    issn: Optional[IssnInfo] = None
    website: Optional[str] = None
    tier: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    notes: Optional[str] = None

    fetcher: str = "crossref"
    fetcher_params: VenueFetcherParams = Field(default_factory=VenueFetcherParams)

    model_config = ConfigDict(extra="allow")


class VenuesConfig(BaseModel):
    """
    顶层 venues 配置。
    示例:
    venues:
      - id: nar
        ...
      - id: bioinformatics
        ...
    """
    venues: List[VenueConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    def by_id(self) -> Dict[str, VenueConfig]:
        return {v.id: v for v in self.venues}


# ====== 通用 YAML 加载函数 ======

def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)  # type: ignore[no-untyped-call]
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a mapping at top level.")
    return data


# ====== 对外暴露的加载函数 ======

@lru_cache(maxsize=None)
def load_keywords(path: Optional[Path] = None) -> KeywordsConfig:
    """
    加载并校验 keywords 配置。

    :param path: 可选，显式指定 keywords.yml 路径；不传则默认使用 config/keywords.yml
    """
    cfg_path = path or (CONFIG_DIR / "keywords.yml")
    raw = _load_yaml(cfg_path)
    try:
        return KeywordsConfig(**raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid keywords config: {cfg_path}") from exc


@lru_cache(maxsize=None)
def load_sources(path: Optional[Path] = None) -> SourcesConfig:
    """
    加载并校验 sources 配置。

    :param path: 可选，显式指定 sources.yml 路径；不传则默认使用 config/sources.yml
    """
    cfg_path = path or (CONFIG_DIR / "sources.yml")
    raw = _load_yaml(cfg_path)
    try:
        return SourcesConfig(**raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid sources config: {cfg_path}") from exc


@lru_cache(maxsize=None)
def load_venues(path: Optional[Path] = None) -> VenuesConfig:
    """
    加载并校验 venues 配置。

    :param path: 可选，显式指定 venues.yml 路径；不传则默认使用 config/venues.yml
    """
    cfg_path = path or (CONFIG_DIR / "venues.yml")
    raw = _load_yaml(cfg_path)
    try:
        return VenuesConfig(**raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid venues config: {cfg_path}") from exc


__all__ = [
    "KeywordsConfig",
    "SourceConfig",
    "SourcesConfig",
    "IssnInfo",
    "VenueFetcherParams",
    "VenueConfig",
    "VenuesConfig",
    "load_keywords",
    "load_sources",
    "load_venues",
]
