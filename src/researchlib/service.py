"""Multi-source live search with provider isolation and explicit coverage status."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from .config import ProviderSettings, Settings
from .http import HTTPClient
from .merge import merge_records
from .models import PaperRecord, RealtimeSearchResult, SearchFilters
from .providers import CrossrefProvider, EuropePMCProvider, OpenAlexProvider, ProviderRegistry, PubMedProvider
from .providers.arxiv import ArxivProvider
from .query import plain_variants
from .ranking import rank_records


def build_registry(settings: Settings, http: HTTPClient) -> ProviderRegistry:
    registry = ProviderRegistry()
    constructors = {
        "openalex": lambda cfg: OpenAlexProvider(http, cfg.per_page, cfg.semantic_search),
        "europe_pmc": lambda cfg: EuropePMCProvider(http, cfg.per_page),
        "pubmed": lambda cfg: PubMedProvider(http, cfg.per_page),
        "crossref": lambda cfg: CrossrefProvider(http, cfg.per_page),
        "arxiv": lambda cfg: ArxivProvider(http, min(cfg.per_page, 100), settings.tracking.arxiv_min_interval),
    }
    for name, constructor in constructors.items():
        default = ProviderSettings(enabled=True)  # enables arXiv without a config migration
        config = settings.providers.get(name, default)
        if config.enabled:
            registry.register(constructor(config))
    return registry


@dataclass
class ProviderResult:
    records: list[PaperRecord] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    truncated: bool = False


class SearchService:
    def __init__(self, registry: ProviderRegistry, settings: Settings):
        self.registry, self.settings = registry, settings

    def search(
        self, queries: list[str], *, mode: str = "quick", result_limit: int = 10,
        candidate_limit: int | None = None, filters: SearchFilters | None = None,
    ) -> RealtimeSearchResult:
        if mode not in {"quick", "deep"}:
            raise ValueError("mode must be quick or deep")
        queries = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
        if not queries:
            raise ValueError("at least one non-empty query is required")
        filters = filters or SearchFilters()
        if filters.from_year and filters.to_year and filters.from_year > filters.to_year:
            raise ValueError("from_year must be <= to_year")
        target = candidate_limit if candidate_limit is not None else (
            self.settings.search.quick_candidate_limit if mode == "quick"
            else self.settings.search.deep_candidate_limit
        )
        if target < 1:
            raise ValueError("candidate_limit must be positive")
        limit = min(max(result_limit, 1), 100)
        providers = self.registry.enabled()
        quota = max(1, math.ceil(target / max(len(providers), 1)))
        collected: list[PaperRecord] = []
        failures: dict[str, str] = {}
        if not providers:
            failures["configuration"] = "no enabled search providers"
        candidate_truncated = False
        with ThreadPoolExecutor(max_workers=min(len(providers), 8) or 1) as executor:
            futures = {
                executor.submit(self._fetch_provider, provider, queries, quota, filters, mode): provider.name
                for provider in providers
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    found = future.result()
                    collected.extend(found.records)
                    failures.update(found.failures)
                    candidate_truncated = candidate_truncated or found.truncated
                except Exception as exc:
                    failures[name] = f"{type(exc).__name__}: {str(exc)[:240]}"
        merged = merge_records(collected)
        ranked = rank_records(merged, queries)
        display_truncated = len(ranked) > limit
        return RealtimeSearchResult(
            queries=queries, raw_candidates=len(collected), unique_candidates=len(merged),
            truncated=candidate_truncated or display_truncated,
            candidates_truncated=candidate_truncated, display_truncated=display_truncated,
            complete=not candidate_truncated and not failures,
            failures=failures, results=ranked[:limit],
        )

    def _fetch_provider(
        self, provider, queries: list[str], quota: int, filters: SearchFilters, mode: str = "quick",
    ) -> ProviderResult:
        result = ProviderResult()
        semantic_enabled = mode == "deep" and provider.name == "openalex" and getattr(provider, "semantic_search", False)
        semantic_quota = min(50, max(1, quota // 4)) if semantic_enabled else 0
        normal_quota = max(1, quota - semantic_quota)
        provider_queries: list[tuple[str, str]] = []
        for query_index, query in enumerate(queries):
            if provider.name in {"crossref", "openalex"}:
                variants = plain_variants(query, max_variants=3 if mode == "quick" else 6)
                provider_queries.extend((f"q{query_index + 1}v{i + 1}", variant)
                                        for i, variant in enumerate(variants))
            else:
                provider_queries.append((f"q{query_index + 1}", query))
        query_quota = max(1, math.ceil(normal_quota / max(len(provider_queries), 1)))
        for query_key, query in provider_queries:
            self._fetch_query(provider, query, query_key, query_quota, filters, result)
        if semantic_enabled:
            semantic_filters = filters.model_copy(update={"semantic": True})
            # Semantic search has a 50-record cap; one broad query avoids wasting API credits.
            self._fetch_query(provider, queries[0], "semantic:q1", semantic_quota, semantic_filters, result)
        return result

    def _fetch_query(
        self, provider, query: str, query_key: str, quota: int,
        filters: SearchFilters, result: ProviderResult,
    ) -> None:
        cursor = None
        examined = 0
        accepted = 0
        page_size_attr = "per_page" if hasattr(provider, "per_page") else "page_size" if hasattr(provider, "page_size") else None
        initial_page_size = getattr(provider, page_size_attr) if page_size_attr else None
        try:
            if page_size_attr:
                setattr(provider, page_size_attr, min(initial_page_size, quota))
            for _ in range(self.settings.search.max_pages_per_query):
                page = provider.discover(query, filters, cursor)
                examined_before_page = examined
                for record in page.records:
                    examined += 1
                    # Enforce the year range even if a remote API silently ignores it.
                    if filters.from_year or filters.to_year:
                        if record.year is None or (filters.from_year and record.year < filters.from_year) or (
                            filters.to_year and record.year > filters.to_year
                        ):
                            continue
                    accepted += 1
                    record.source_rank = examined
                    record.query_ranks[f"{provider.name}:{query_key}"] = examined
                    result.records.append(record)
                    if accepted >= quota:
                        break
                if accepted >= quota:
                    if page.next_cursor or len(page.records) > examined - examined_before_page:
                        result.truncated = True
                    # A filled page may still have undisclosed results; report conservatively.
                    if not page.done:
                        result.truncated = True
                    break
                if page.done or not page.next_cursor:
                    break
                cursor = page.next_cursor
            else:
                if cursor:
                    result.truncated = True
        except Exception as exc:
            result.failures[f"{provider.name}:{query_key}"] = f"{type(exc).__name__}: {str(exc)[:240]}"
        finally:
            if page_size_attr:
                setattr(provider, page_size_attr, initial_page_size)
