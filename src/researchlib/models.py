from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class Author(BaseModel):
    name: str
    orcid: str | None = None


class PaperRecord(BaseModel):
    title: str
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    published_date: str | None = None
    journal: str | None = None
    document_type: str | None = None
    doi: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    abstract: str | None = None
    abstract_source: str | None = None
    url: str | None = None
    oa_url: str | None = None
    publication_status: str | None = None
    preprint_version: str | None = None
    published_doi: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str
    source_rank: int | None = None
    query_ranks: dict[str, int] = Field(default_factory=dict)
    citation_count: int | None = None
    field_sources: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class DiscoveryPage(BaseModel):
    records: list[PaperRecord]
    next_cursor: str | None = None
    done: bool = False
    actual_query: str
    request_count: int = 1


class SearchFilters(BaseModel):
    from_year: int | None = None
    to_year: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    mesh: list[str] = Field(default_factory=list)
    semantic: bool = False
    bulk: bool = False


class SearchHit(BaseModel):
    paper: PaperRecord
    sources: list[str]
    source_ranks: dict[str, int]
    score: float
    keyword_score: float
    bm25_score: float


class RealtimeSearchResult(BaseModel):
    queries: list[str]
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_candidates: int
    unique_candidates: int
    truncated: bool
    candidates_truncated: bool = False
    display_truncated: bool = False
    complete: bool = False
    failures: dict[str, str] = Field(default_factory=dict)
    results: list[SearchHit]


class TrackResult(BaseModel):
    source_type: str
    window_start: str
    window_end: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_candidates: int
    unique_candidates: int
    truncated: bool
    failures: dict[str, str] = Field(default_factory=dict)
    next_cursors: dict[str, str] = Field(default_factory=dict)
    results: list[PaperRecord]


class RelatedPaperList(BaseModel):
    total: int | None = None
    raw_candidates: int = 0
    unique_candidates: int = 0
    candidates_truncated: bool = False
    display_truncated: bool = False
    truncated: bool = False
    complete: bool = False
    failures: dict[str, str] = Field(default_factory=dict)
    results: list[PaperRecord] = Field(default_factory=list)


class RelatedPapersResult(BaseModel):
    doi: str
    status: str
    limit: int
    source: str = "openalex"
    sort: str = "citation_count:desc"
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    paper: PaperRecord | None = None
    references: RelatedPaperList = Field(default_factory=RelatedPaperList)
    citations: RelatedPaperList = Field(default_factory=RelatedPaperList)
    complete: bool = False
    failures: dict[str, str] = Field(default_factory=dict)
    error: str | None = None
