from __future__ import annotations

from paper_tracker.sources.base import BaseMetadataSource, SourceEnrichmentResult, create_source, list_registered_source_types, register_source_type

# 导入内置 source，实现注册（side effects）
# from paper_tracker.sources import crossref  # noqa: F401
# from paper_tracker.sources import pubmed   # noqa: F401
# from paper_tracker.sources import pmc      # noqa: F401
# from paper_tracker.sources import biorxiv  # noqa: F401
# from paper_tracker.sources import arxiv    # noqa: F401

__all__ = [
    "BaseMetadataSource",
    "SourceEnrichmentResult",
    "register_source_type",
    "create_source",
    "list_registered_source_types",
]
