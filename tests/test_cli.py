"""CLI/MCP parity with mocked services; no live API calls."""
import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from researchlib import cli
from researchlib.models import (
    PaperRecord, RealtimeSearchResult, RelatedPaperList, RelatedPapersResult,
    SearchHit, TrackResult,
)

runner = CliRunner()

# Each public tool has a CLI route; kwargs are the complete MCP defaults/overrides.
CASES = [
    ("search_literature", ["search", "-q", "protein"], dict(
        query="protein", topic=None, mode="quick", limit=10, from_year=None, to_year=None, output="compact")),
    ("search_literature", ["search", "-q", "protein", "--topic", "small-orfs", "--mode", "deep",
                           "--from-year", "2020", "--to-year", "2026", "--limit", "150", "--output", "full"],
     dict(query="protein", topic="small-orfs", mode="deep", limit=150, from_year=2020, to_year=2026, output="full")),
    ("get_paper_details", ["paper-details", "--identifier", "10.1000/fixture", "--identifiers", "pmid:42"],
     dict(identifiers=["10.1000/fixture", "pmid:42"])),
    ("get_related_papers", ["related-papers", "--doi", "10.1000/fixture"],
     dict(doi="10.1000/fixture", limit=10, output="compact")),
    ("get_related_papers", ["related-papers", "--doi", "10.1000/fixture", "--limit", "3", "--output", "full"],
     dict(doi="10.1000/fixture", limit=3, output="full")),
    ("lookup_doi", ["lookup-doi", "--doi", "10.1000/fixture"], dict(doi="10.1000/fixture")),
    ("track_venues", ["track"], dict(days=7, venue=None, limit=10, output="compact")),
    ("track_venues", ["track", "--source", "venues", "--days", "0", "--limit", "0",
                      "--venue", "bioinformatics", "--output", "full"],
     dict(days=0, venue="bioinformatics", limit=0, output="full")),
    ("track_preprints", ["track", "--source", "preprints"],
     dict(days=7, topic=None, sources=None, limit=10, output="compact")),
    ("track_preprints", ["track", "--source", "preprints", "--days", "14", "--topic", "small-orfs",
                         "--sources", "arxiv", "--sources", "biorxiv", "--limit", "20", "--output", "full"],
     dict(days=14, topic="small-orfs", sources=["arxiv", "biorxiv"], limit=20, output="full")),
    ("list_topics", ["topics"], {}),
    ("list_venues", ["venues"], {}),
    ("expand_query", ["expand-query", "--topic", "small-orfs"], dict(topic="small-orfs")),
]


@pytest.fixture
def mock_services(monkeypatch):
    tools = cli.tools
    monkeypatch.setattr(tools, "_shared_http", lambda: object())
    paper = PaperRecord(title="Fixture paper", doi="10.1000/fixture", year=2025,
                        source="openalex", abstract="Fixture abstract", citation_count=12)
    search = RealtimeSearchResult(
        queries=["protein"], raw_candidates=1, unique_candidates=1, truncated=False, complete=True,
        results=[SearchHit(paper=paper, sources=["openalex"], source_ranks={"openalex": 1},
                           score=1.5, keyword_score=1, bm25_score=0.5)],
    )
    track = TrackResult(source_type="venues", window_start="2026-11-01", window_end="2026-11-07",
                        raw_candidates=1, unique_candidates=1, truncated=False, results=[paper])
    related = RelatedPapersResult(
        doi=paper.doi, status="ok", limit=10, paper=paper, complete=True,
        references=RelatedPaperList(total=1, complete=True, results=[paper]),
        citations=RelatedPaperList(total=0, complete=True),
    )
    monkeypatch.setattr(tools.SearchService, "search", lambda *a, **kw: search)
    monkeypatch.setattr(tools.RealtimeTracker, "track_venues", lambda *a, **kw: track)
    monkeypatch.setattr(tools.RealtimeTracker, "track_preprints", lambda *a, **kw: track)
    monkeypatch.setattr(tools.RelatedPapersService, "fetch", lambda *a, **kw: related)
    monkeypatch.setattr(tools.DetailsService, "fetch", lambda *a, **kw: {
        "results": [{"requested_id": paper.doi, "sources": ["openalex"], "record": paper.model_dump(mode="json")}],
        "invalid": [], "not_found": [], "unresolved": [], "failures": {}, "complete": True,
    })
    monkeypatch.setattr(tools.CrossrefProvider, "enrich", lambda *a, **kw: [paper])


@pytest.mark.parametrize("name,args,kwargs", CASES)
def test_json_matches_real_mcp_serialization_and_parameters(mock_services, name, args, kwargs):
    tool = getattr(cli.tools, name)
    expected = tool(**kwargs)
    with patch.object(cli.tools, name, wraps=tool) as called:
        result = runner.invoke(cli.app, [*args, "--format", "json"], color=True)
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout) == expected
        assert "\x1b[" not in result.stdout and not result.stderr
        called.assert_called_once_with(**kwargs)


@pytest.mark.parametrize("name,args,kwargs", CASES)
def test_default_human_output_still_runs(mock_services, name, args, kwargs):
    result = runner.invoke(cli.app, args, color=True)
    assert result.exit_code == 0, result.output
    assert result.stdout.strip()
    assert not result.stdout.lstrip().startswith("{")
    assert "\x1b[" not in result.stdout


@pytest.mark.parametrize("args,name,kwargs", [
    (["search", "-q", "protein", "--from-year", "2026", "--to-year", "2020"], "search_literature",
     dict(query="protein", from_year=2026, to_year=2020)),
    (["search", "-q", "protein", "--topic", "unknown-topic"], "search_literature",
     dict(query="protein", topic="unknown-topic")),
    (["lookup-doi", "--doi", "invalid"], "lookup_doi", dict(doi="invalid")),
    (["related-papers", "--doi", "invalid"], "get_related_papers", dict(doi="invalid")),
    (["paper-details", "--identifier", "invalid"], "get_paper_details", dict(identifiers=["invalid"])),
    (["paper-details", *[value for _ in range(16) for value in ("--identifier", "pmid:1")]],
     "get_paper_details", dict(identifiers=["pmid:1"] * 16)),
    (["track", "--source", "preprints", "--sources", "invalid"], "track_preprints", dict(sources=["invalid"])),
    (["track", "--source", "venues", "--venue", "unknown-venue"], "track_venues", dict(venue="unknown-venue")),
    (["expand-query", "--topic", "unknown-topic"], "expand_query", dict(topic="unknown-topic")),
])
def test_json_preserves_mcp_validation_results_without_network(monkeypatch, args, name, kwargs):
    monkeypatch.setattr(cli.tools, "_shared_http", lambda: object())
    expected = getattr(cli.tools, name)(**kwargs)
    result = runner.invoke(cli.app, [*args, "--format", "json"])
    assert result.exit_code == 0, result.output
    # Invalid related lookups include a per-call timestamp, not fixed business data.
    actual = json.loads(result.stdout)
    if name == "get_related_papers":
        actual.pop("generated_at")
        expected.pop("generated_at")
    assert actual == expected


@pytest.mark.parametrize("args", [
    ["search", "-q", "protein", "--mode", "invalid"],
    ["search", "-q", "protein", "--output", "invalid"],
    ["track", "--source", "invalid"],
    ["topics", "--format", "invalid"],
])
def test_enum_validation_rejects_invalid_options(args):
    assert runner.invoke(cli.app, args).exit_code == 2


def test_doctor_retains_text_and_supports_json():
    text = runner.invoke(cli.app, ["doctor"])
    assert text.exit_code == 0 and text.stdout.startswith("mode=stateless providers=")
    result = runner.invoke(cli.app, ["doctor", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["mode"] == "stateless" and isinstance(data["providers"], list)
    assert data["topics"] > 0 and data["venues"] > 0


@pytest.mark.parametrize("tty,no_color,expected_color", [
    (True, False, True), (False, False, False), (True, True, False),
])
def test_tty_color_and_no_color_environment(monkeypatch, capsys, tty, no_color, expected_color):
    monkeypatch.setattr(cli.sys.stdout, "isatty", lambda: tty)
    monkeypatch.delenv("NO_COLOR", raising=False)
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    cli._echo("Readable status", fg="green", bold=True)
    output = capsys.readouterr().out
    assert ("\x1b[" in output) == expected_color
    assert "Readable status" in output


def test_numbered_papers_wrap_titles_and_abstracts_without_losing_fields(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "48")
    title = "Evidence for functional small proteins " * 5
    abstract = "Translation was observed across samples. " * 10
    cli._paper({"title": title, "year": 2025, "doi": "10.1000/fixture", "abstract": abstract},
               1, sources=["openalex", "pubmed"], score=0.0, abstract=True)
    output = capsys.readouterr().out
    normalized = " ".join(output.split())
    assert output.startswith("1. Evidence")
    assert " ".join(title.split()) in normalized
    assert " ".join(abstract.split()) in normalized
    assert "Year: 2025" in output and "DOI: 10.1000/fixture" in output
    assert "Sources: openalex, pubmed | score: 0.0000" in output
    assert all(len(line) <= 48 for line in output.splitlines())
    assert "\x1b[" not in output


def test_summary_retains_counts_and_failure_details(monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "100")
    cli._summary({"raw_candidates": 10, "unique_candidates": 8, "truncated": True,
                  "failures": {"openalex": "request budget exceeded"}})
    output = capsys.readouterr().out
    assert "raw=10 | unique=8 | truncated=yes | failures=1" in output
    assert "Warning [openalex]: request budget exceeded" in output
