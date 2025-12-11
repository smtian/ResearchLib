# src/paper_tracker/venues/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Type

from paper_tracker.config import VenueConfig, VenuesConfig
from paper_tracker.models import Paper


class BaseVenueFetcher(ABC):
    """
    所有期刊抓取器的抽象基类。

    每个具体实现（例如 Crossref 抓取器）负责：
    - 根据 VenueConfig 中的元数据（issn、name 等）构造查询
    - 调用相应 API / RSS
    - 将结果转换为 Paper 列表（不要求此时带摘要和 pdf_url）
    """

    def __init__(self, venue: VenueConfig) -> None:
        self.venue = venue

    @abstractmethod
    def fetch_recent(self, days_back: int) -> List[Paper]:
        """
        抓取最近 days_back 天内该期刊的新增/更新论文。
        返回 Paper 列表（至少包含 id/title/doi/journal/venue_id/published_at 等基础信息）。
        """
        raise NotImplementedError


# ===== 抓取器注册表 & 工厂 =====

_FETCHER_REGISTRY: Dict[str, Type[BaseVenueFetcher]] = {}


def register_fetcher(name: str):
    """
    装饰器：将抓取器类注册到全局注册表中。

    用法示例（在 crossref.py 中）：
        @register_fetcher("crossref")
        class CrossrefVenueFetcher(BaseVenueFetcher):
            ...
    """

    normalized = name.lower().strip()

    def decorator(cls: Type[BaseVenueFetcher]) -> Type[BaseVenueFetcher]:
        if normalized in _FETCHER_REGISTRY:
            raise RuntimeError(f"Fetcher '{normalized}' already registered")
        _FETCHER_REGISTRY[normalized] = cls
        return cls

    return decorator


def create_fetcher(venue: VenueConfig) -> BaseVenueFetcher:
    """
    根据 VenueConfig.fetcher 字段创建对应的抓取器实例。

    例如：
      venue.fetcher == "crossref"  ->  使用注册的 CrossrefVenueFetcher
    """
    fetcher_name = (venue.fetcher or "").lower().strip()
    if not fetcher_name:
        raise ValueError(f"Venue '{venue.id}' has empty fetcher type")

    try:
        cls = _FETCHER_REGISTRY[fetcher_name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown venue fetcher type '{fetcher_name}' for venue '{venue.id}'"
        ) from exc

    return cls(venue)


def create_fetchers_for_config(venues_config: VenuesConfig) -> List[BaseVenueFetcher]:
    """
    便捷方法：根据 VenuesConfig 为所有 venue 创建抓取器列表。
    """
    fetchers: List[BaseVenueFetcher] = []
    for venue in venues_config.venues:
        # 理论上每个 venue 都有 fetcher 字段，若无则跳过
        if not venue.fetcher:
            continue
        fetchers.append(create_fetcher(venue))
    return fetchers
