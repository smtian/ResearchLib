"""MCP tool-contract tests without an installed SDK or external API calls.

These exercise the actual mcp_server.py functions with a minimal registration stub.
They are *not* a substitute for the real SDK client test in test_mcp_server.py.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from researchlib.models import Author, PaperRecord, RealtimeSearchResult, SearchHit, TrackResult
from researchlib.related import RelatedPapersService

MODULE_FILE = Path(__file__).resolve().parents[1] / "src/researchlib/mcp_server.py"
EXPECTED_TOOLS = {
    "search_literature", "get_paper_details", "lookup_doi", "track_venues",
    "track_preprints", "list_topics", "expand_query", "list_venues",
    "get_related_papers",
}


@pytest.fixture
def server_module(monkeypatch):
    class FakeMCPServer:
        def __init__(self, *args, **kwargs):
            self.registered = {}
            self.description = kwargs.get("description")

        def tool(self):
            def register(func):
                self.registered[func.__name__] = func
                return func
            return register

        def run(self):
            raise AssertionError("no real stdio server must start in unit tests")

    mcp = types.ModuleType("mcp")
    mcp.__path__ = []
    server = types.ModuleType("mcp.server")
    server.__path__ = []
    mcpserver = types.ModuleType("mcp.server.mcpserver")
    mcpserver.MCPServer = FakeMCPServer
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", server)
    monkeypatch.setitem(sys.modules, "mcp.server.mcpserver", mcpserver)

    spec = importlib.util.spec_from_file_location("researchlib._test_mcp_contract", MODULE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def search_result():
    paper = PaperRecord(
        title="Microprotein coding potential study", year=2025,
        authors=[Author(name="First Author"), Author(name="Other Author")],
        doi="10.1000/microprotein", abstract="This abstract is deliberately private to full output.",
        journal="Journal of Example Biology", source="pubmed", pmid="12345",
        url="https://example.org/paper", query_ranks={"pubmed:q1": 1},
    )
    return RealtimeSearchResult(
        queries=["microprotein"], raw_candidates=3, unique_candidates=1,
        candidates_truncated=False, display_truncated=True, truncated=True,
        complete=True, failures={},
        results=[SearchHit(
            paper=paper, sources=["pubmed", "europe_pmc"], source_ranks={"pubmed:q1": 1},
            score=3.1, keyword_score=1, bm25_score=2.1,
        )],
    )


@pytest.fixture
def track_result(search_result):
    return TrackResult(
        source_type="preprints", window_start="2026-11-01", window_end="2026-11-12",
        raw_candidates=2, unique_candidates=1, truncated=True,
        failures={"biorxiv": "rate limit"}, next_cursors={"arxiv": "next"},
        results=[search_result.results[0].paper],
    )


def test_exact_tool_registration_contract(server_module):
    assert set(server_module.mcp.registered) == EXPECTED_TOOLS
    assert len(server_module.mcp.registered) == 9


def test_default_compact_search_excludes_abstract_and_large_fields(server_module, search_result):
    compact = server_module._search_payload(search_result, "compact")
    row = compact["results"][0]
    assert row["id"] == "10.1000/microprotein"
    assert row["title"] == "Microprotein coding potential study"
    assert row["first_author"] == "First Author"
    assert row["abstract_available"] is True
    assert row["sources"] == ["pubmed", "europe_pmc"]
    assert row["score"] == 3.1
    assert not set(row) & {"abstract", "authors", "paper", "raw", "field_sources", "source_ranks"}
    assert compact["complete"] is True
    assert compact["display_truncated"] is True
    assert compact["candidates_truncated"] is False
    assert "abstract is deliberately private" not in str(compact)


def test_full_search_keeps_nested_abstract_and_rank_components(server_module, search_result):
    full = server_module._search_payload(search_result, "full")
    hit = full["results"][0]
    assert hit["paper"]["abstract"].startswith("This abstract")
    assert len(hit["paper"]["authors"]) == 2
    assert hit["keyword_score"] == 1
    assert hit["bm25_score"] == 2.1


def test_tracking_compact_and_full_preserve_coverage(server_module, track_result):
    compact = server_module._track_payload(track_result, "compact")
    assert compact["results"][0]["abstract_available"] is True
    assert "abstract" not in compact["results"][0]
    assert compact["next_cursors"] == {"arxiv": "next"}
    assert compact["failures"] == {"biorxiv": "rate limit"}
    assert "complete" not in compact
    full = server_module._track_payload(track_result, "full")
    assert full["results"][0]["abstract"].startswith("This abstract")


def test_search_public_tool_routes_mode_limit_and_year_filters(server_module, search_result, monkeypatch):
    captured = {}

    def fake_search(self, queries, **kwargs):
        captured.update(kwargs)
        captured["queries"] = queries
        return search_result

    monkeypatch.setattr(server_module.SearchService, "search", fake_search)
    monkeypatch.setattr(server_module, "_shared_http", lambda: object())
    response = server_module.search_literature(
        "microprotein", mode="deep", limit=150, from_year=2020, to_year=2026
    )
    assert captured["mode"] == "deep"
    assert captured["result_limit"] == 100
    assert captured["filters"].from_year == 2020
    assert captured["filters"].to_year == 2026
    assert response["results"][0]["abstract_available"] is True
    assert "abstract" not in response["results"][0]


def test_search_rejects_unknown_topic_and_backward_years(server_module, monkeypatch):
    assert "unknown topic" in server_module.search_literature("microprotein", topic="not-a-topic")["error"]
    assert "from_year" in server_module.search_literature("microprotein", from_year=2025, to_year=2020)["error"]


def test_details_tool_passes_identifiers_without_hidden_persistence(server_module, monkeypatch):
    captured = []

    def fake_fetch(self, identifiers):
        captured.extend(identifiers)
        return {"results": [], "invalid": [], "not_found": identifiers, "failures": {}, "complete": False}

    monkeypatch.setattr(server_module.DetailsService, "fetch", fake_fetch)
    monkeypatch.setattr(server_module, "_shared_http", lambda: object())
    result = server_module.get_paper_details(["pmid:12345", "openalex:W123"])
    assert captured == ["pmid:12345", "openalex:W123"]
    assert result["not_found"] == captured
    assert result["complete"] is False


def test_invalid_doi_status_without_network(server_module):
    result = server_module.lookup_doi("not a DOI")
    assert result["verified"] is False
    assert result["status"] == "invalid"


def test_related_papers_compact_full_and_limit_routing(server_module, monkeypatch):
    import json

    from researchlib.models import RelatedPaperList, RelatedPapersResult

    captured = []
    paper = PaperRecord(
        title="Example citation", doi="10.1000/example", openalex_id="W1",
        source="openalex", citation_count=42, abstract="Evidence " * 1000,
        url="https://openalex.org/W1", authors=[Author(name="Author") for _ in range(30)],
        raw={"private": "raw payload"},
    )
    fixture = RelatedPapersResult(
        doi="10.1000/example", status="ok", limit=100, paper=paper, complete=True,
        references=RelatedPaperList(results=[paper], total=1, complete=True),
        citations=RelatedPaperList(results=[paper], total=1, complete=True),
    )

    def fetch(self, doi, limit):
        captured.append((doi, limit))
        return fixture

    monkeypatch.setattr(RelatedPapersService, "fetch", fetch)
    monkeypatch.setattr(server_module, "_shared_http", lambda: object())
    compact = server_module.get_related_papers("10.1000/example", limit=150)
    full = server_module.get_related_papers("10.1000/example", output="full")
    assert captured == [("10.1000/example", 150), ("10.1000/example", 10)]
    for row in (compact["paper"], compact["references"]["results"][0], compact["citations"]["results"][0]):
        assert row["citation_count"] == 42
        assert row["sources"] == ["openalex"]
        assert row["abstract_available"] is True
        assert row["openalex_id"] == "W1"
        assert row["url"] == "https://openalex.org/W1"
        assert not set(row) & {"abstract", "authors", "raw"}
    assert full["references"]["results"][0]["abstract"] == paper.abstract
    assert full["references"]["results"][0]["citation_count"] == 42
    assert "raw payload" not in json.dumps(full)
    assert len(json.dumps(compact)) < len(json.dumps(full)) / 3
    assert compact["complete"] is True


def test_preprint_public_tool_preserves_sources_and_output_option(server_module, track_result, monkeypatch):
    captured = {}

    def fake_track(self, **kwargs):
        captured.update(kwargs)
        return track_result

    monkeypatch.setattr(server_module.RealtimeTracker, "track_preprints", fake_track)
    monkeypatch.setattr(server_module, "_shared_http", lambda: object())
    result = server_module.track_preprints(days=7, sources=["arxiv"], limit=10, output="compact")
    assert captured["sources"] == ["arxiv"]
    assert "abstract" not in result["results"][0]
    assert result["truncated"] is True
    assert "biorxiv" in result["failures"]
