# src/paper_tracker/venues/__init__.py
from .base import (
    BaseVenueFetcher,
    create_fetcher,
    create_fetchers_for_config,
    register_fetcher,
)

# 导入所有内置 fetcher，确保注册器被填充
from . import crossref  # noqa: F401

__all__ = [
    "BaseVenueFetcher",
    "create_fetcher",
    "create_fetchers_for_config",
    "register_fetcher",
]
