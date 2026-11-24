"""Stateless MCP interface. Only a bounded, in-process HTTP cache is retained."""
from __future__ import annotations

import atexit
from functools import lru_cache
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from .config import load_settings
from .details import DetailsService
from .http import HTTPClient
from .models import PaperRecord, SearchFilters
from .providers.crossref import CrossrefProvider
from .providers.utils import normalize_doi
from .related import RelatedPapersService
from .service import SearchService, build_registry
from .topics import expand_topic, expand_user_query, load_topics
from .tracking import RealtimeTracker
from .venues import load_venues

mcp = MCPServer(
    "researchlib",
    description="Stateless live scholarly discovery, conservative deduplication and on-demand abstracts",
    instructions=(
        "Start with search_literature(output='compact') for a small ranked candidate list. "
        "Use get_paper_details with up to 15 identifiers to retrieve complete abstracts only when needed. "
        "Use get_related_papers(doi) for references and citing papers sorted by citation count. "
        "Use output='full' to retrieve all search fields in one call. Treat incomplete/failed searches "
        "as partial evidence. No PDF/full text, LLM API or persistent paper library is used."
    ),
    version="0.2.0",
)


def _dump(value) -> dict[str, Any]:
    return value.model_dump(mode="json")


@lru_cache(maxsize=1)
def _shared_http() -> HTTPClient:
    """Cache across MCP calls (and reset request budget every configured window)."""
    http = HTTPClient(load_settings().http)
    atexit.register(http.close)
    return http


def _compact_paper(paper: PaperRecord) -> dict[str, Any]:
    return {
        "id": paper.doi or (f"pmid:{paper.pmid}" if paper.pmid else None)
        or (f"arxiv:{paper.arxiv_id}" if paper.arxiv_id else None)
        or (f"openalex:{paper.openalex_id}" if paper.openalex_id else None),
        "title": paper.title,
        "year": paper.year,
        "published_date": paper.published_date,
        "first_author": paper.authors[0].name if paper.authors else None,
        "journal": paper.journal,
        "doi": paper.doi,
        "pmid": paper.pmid,
        "arxiv_id": paper.arxiv_id,
        "openalex_id": paper.openalex_id,
        "url": paper.url or paper.oa_url,
        "abstract_available": bool(paper.abstract),
        "publication_status": paper.publication_status,
    }


def _search_payload(result, output: str) -> dict[str, Any]:
    data = _dump(result)
    if output == "compact":
        data["results"] = [
            {**_compact_paper(hit.paper), "score": hit.score, "sources": hit.sources}
            for hit in result.results
        ]
    return data


def _track_payload(result, output: str) -> dict[str, Any]:
    data = _dump(result)
    if output == "compact":
        data["results"] = [_compact_paper(paper) for paper in result.results]
    return data


@mcp.tool()
def search_literature(
    query: str,
    topic: str | None = None,
    mode: Literal["quick", "deep"] = "quick",
    limit: int = 10,
    from_year: int | None = None,
    to_year: int | None = None,
    output: Literal["compact", "full"] = "compact",
) -> dict[str, Any]:
    """Search OpenAlex/Europe PMC/PubMed/Crossref/arXiv, rank and deduplicate.

    Default output saves context tokens. Use get_paper_details for complete abstracts;
    output='full' preserves the original nested paper/hit representation.
    """
    settings = load_settings()
    topics = load_topics(settings.tracking.topics_file)
    if topic and topic not in topics:
        return {"error": f"unknown topic: {topic}", "available_topics": sorted(topics)}
    if from_year and to_year and from_year > to_year:
        return {"error": "from_year must be <= to_year"}
    try:
        queries = expand_user_query(query, topics)
    except ValueError as exc:
        return {"error": str(exc)}
    if topic:
        queries.extend(expand_topic(topics[topic]))
    result = SearchService(build_registry(settings, _shared_http()), settings).search(
        list(dict.fromkeys(queries)), mode=mode, result_limit=min(max(limit, 1), 100),
        filters=SearchFilters(from_year=from_year, to_year=to_year),
    )
    return _search_payload(result, output)


@mcp.tool()
def get_paper_details(identifiers: list[str]) -> dict[str, Any]:
    """Retrieve metadata and full abstracts by DOI, PMID, OpenAlex ID or arXiv ID.

    Examples: ['10.1038/s41586-024-...', 'pmid:12345678', 'openalex:W123',
    'arxiv:2401.12345']. Returns explicit invalid/missing/failure statuses.
    Maximum 15 identifiers per call; no full-text downloads.
    """
    return DetailsService(_shared_http()).fetch(identifiers)


@mcp.tool()
def get_related_papers(
    doi: str, limit: int = 10, output: Literal["compact", "full"] = "compact",
) -> dict[str, Any]:
    """Find references (outgoing) and citations (incoming) via OpenAlex by DOI.

    Each list returns its global top limit (1–100) by each work's citation_count
    descending, not relevance. Coverage is limited to OpenAlex's indexed links.
    Inspect status, failures and each list's truncation flags before using results.
    """
    result = RelatedPapersService(_shared_http()).fetch(doi, limit)
    data = _dump(result)
    if output == "compact":
        def compact(paper):
            return {**_compact_paper(paper), "citation_count": paper.citation_count,
                    "sources": [paper.source]}
        data["paper"] = compact(result.paper) if result.paper else None
        for relation in ("references", "citations"):
            data[relation]["results"] = [compact(paper) for paper in getattr(result, relation).results]
    return data


@mcp.tool()
def lookup_doi(doi: str) -> dict[str, Any]:
    """Check one DOI against Crossref (for abstracts, prefer get_paper_details)."""
    normalized = normalize_doi(doi)
    if not normalized:
        return {"doi": doi, "verified": False, "status": "invalid", "error": "invalid DOI format"}
    try:
        records = CrossrefProvider(_shared_http(), 1).enrich([{"doi": normalized}])
        if not records:
            return {"doi": normalized, "verified": False, "status": "not_found", "record": None}
        return {"doi": normalized, "verified": True, "status": "verified", "record": records[0].model_dump(mode="json")}
    except Exception as exc:
        return {"doi": normalized, "verified": False, "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:240]}"}


@mcp.tool()
def track_venues(
    days: int = 7, venue: str | None = None, limit: int = 10,
    output: Literal["compact", "full"] = "compact",
) -> dict[str, Any]:
    """Live journal publication-date window; explicit pagination coverage metadata."""
    settings = load_settings()
    try:
        result = RealtimeTracker(_shared_http(), settings).track_venues(
            days=max(days, 1), venue=venue, limit=min(max(limit, 1), 100)
        )
        return _track_payload(result, output)
    except KeyError as exc:
        return {"error": str(exc)}


@mcp.tool()
def track_preprints(
    days: int = 7,
    topic: str | None = None,
    sources: list[str] | None = None,
    limit: int = 10,
    output: Literal["compact", "full"] = "compact",
) -> dict[str, Any]:
    """Date-window tracking for arXiv, bioRxiv and medRxiv preprints."""
    settings = load_settings()
    tracker = RealtimeTracker(_shared_http(), settings)
    if topic and topic not in tracker.topics:
        return {"error": f"unknown topic: {topic}", "available_topics": sorted(tracker.topics)}
    try:
        result = tracker.track_preprints(
            days=max(days, 1), topic=topic, sources=sources, limit=min(max(limit, 1), 100)
        )
        return _track_payload(result, output)
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool()
def list_topics() -> dict[str, Any]:
    """List configured scientific topics and deterministic term dictionaries."""
    settings = load_settings()
    return {slug: topic.model_dump(mode="json") for slug, topic in load_topics(settings.tracking.topics_file).items()}


@mcp.tool()
def expand_query(topic: str) -> dict[str, Any]:
    """Show deterministic English query expansions for a configured topic."""
    settings = load_settings()
    topics = load_topics(settings.tracking.topics_file)
    if topic not in topics:
        return {"error": f"unknown topic: {topic}", "available_topics": sorted(topics)}
    return {"topic": topic, "strategy": topics[topic].strategy, "queries": expand_topic(topics[topic])}


@mcp.tool()
def list_venues() -> dict[str, Any]:
    """List enabled tracked journals, ISSNs and tags."""
    settings = load_settings()
    return {
        slug: venue.model_dump(mode="json")
        for slug, venue in load_venues(settings.tracking.venues_file).items() if venue.enabled
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
