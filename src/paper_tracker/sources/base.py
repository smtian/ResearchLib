from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Protocol, Type, List

from paper_tracker.models import Paper
from paper_tracker.config import SourceConfig


@dataclass
class SourceQuery:
    kind: str   # "doi" | "pmid" | "pmcid" | "title" 等
    value: str


def build_default_queries(search_by: List[str], paper: Paper) -> List[SourceQuery]:
    """
    根据 SourceConfig.search_by 和 Paper 上已有信息构造查询列表，
    保持 search_by 中的顺序。
    """
    queries: List[SourceQuery] = []

    for key in search_by:
        key = key.lower()
        if key == "doi" and paper.doi:
            queries.append(SourceQuery(kind="doi", value=paper.doi))
        elif key == "pmid":
            # 目前 Paper 没有 pmid 字段，将来可以从富集 metadata 中传入
            pass
        elif key == "pmcid":
            # 同上，目前只在 SourceEnrichmentResult.metadata 中使用
            pass
        elif key == "title" and paper.title:
            queries.append(SourceQuery(kind="title", value=paper.title))

    return queries


@dataclass
class SourceEnrichmentResult:
    source_id: str
    pdf_url: Optional[str] = None
    abstract: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)  # pmid, pmcid, license, etc.
    confidence: float = 1.0


class BaseMetadataSource(Protocol):
    """
    一个 source 只负责“尽力补充信息”，不负责 winner 决策。
    """
    id: str

    def enrich(self, paper: Paper) -> SourceEnrichmentResult:
        ...


_SOURCE_REGISTRY: Dict[str, Type[BaseMetadataSource]] = {}


def register_source_type(source_type: str):
    """
    装饰器：按 type 注册 source 实现。

    用法：
      @register_source_type("crossref")
      class CrossrefMetadataSource: ...
    """
    normalized = source_type.lower().strip()

    def decorator(cls: Type[BaseMetadataSource]) -> Type[BaseMetadataSource]:
        if normalized in _SOURCE_REGISTRY:
            raise RuntimeError(f"Source type '{normalized}' already registered")
        _SOURCE_REGISTRY[normalized] = cls
        return cls

    return decorator


def create_source(cfg: SourceConfig) -> BaseMetadataSource:
    """
    根据 sources.yml 的 type 实例化对应的 source。
    """
    t = (cfg.type or "").lower().strip()
    if not t:
        raise ValueError(f"Source '{cfg.id}' has empty type")

    try:
        cls = _SOURCE_REGISTRY[t]
    except KeyError as exc:
        raise ValueError(f"Unknown source type '{t}' for source '{cfg.id}'") from exc

    # 约定：所有实现都接受 cfg 作为构造参数
    return cls(cfg)  # type: ignore[call-arg]


def list_registered_source_types() -> list[str]:
    return sorted(_SOURCE_REGISTRY.keys())
