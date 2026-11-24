"""On-demand metadata lookup and failure reporting; zero network calls."""
from __future__ import annotations

import httpx

from researchlib.config import HTTPSettings
from researchlib.details import DetailsService
from researchlib.http import HTTPClient
from researchlib.models import PaperRecord
from researchlib.providers.crossref import CrossrefProvider
from researchlib.providers.europe_pmc import EuropePMCProvider
from researchlib.providers.pubmed import PubMedProvider


def _offline_client():
    def forbid_request(req):
        raise AssertionError(f"unexpected network request to {req.url}")
    return HTTPClient(HTTPSettings(), transport=httpx.MockTransport(forbid_request))


def test_details_empty_or_too_many_identifiers():
    client = _offline_client()
    try:
        service = DetailsService(client)
        assert "1 to 15" in service.fetch([])["error"]
        assert "1 to 15" in service.fetch(["pmid:1"] * 16)["error"]
    finally:
        client.close()


def test_details_merges_provenance_and_prefers_available_abstract(monkeypatch):
    doi = "10.1000/merge"

    def europe(self, ids):
        assert ids == [{"doi": doi}]
        return [PaperRecord(title="Small proteins", doi=doi, source="europe_pmc", abstract="Observed translation evidence")]

    def crossref(self, ids):
        assert ids == [{"doi": doi}]
        return [PaperRecord(title="Small proteins", doi=doi, source="crossref", journal="Example Journal")]

    monkeypatch.setattr(EuropePMCProvider, "enrich", europe)
    monkeypatch.setattr(CrossrefProvider, "enrich", crossref)
    client = _offline_client()
    try:
        original = "https://doi.org/10.1000/MERGE"
        result = DetailsService(client).fetch([original])
        assert result["complete"] is True
        assert result["invalid"] == [] and result["not_found"] == []
        assert result["failures"] == {}
        assert len(result["results"]) == 1
        paper = result["results"][0]
        assert paper["requested_id"] == original
        assert paper["sources"] == ["crossref", "europe_pmc"]
        assert paper["record"]["abstract"] == "Observed translation evidence"
        assert paper["record"]["doi"] == doi
    finally:
        client.close()


def test_details_invalid_ids_are_reported_not_guessed():
    client = _offline_client()
    try:
        result = DetailsService(client).fetch(["arbitrary words", "garbage"])
        assert result == {
            "results": [], "invalid": ["arbitrary words", "garbage"],
            "not_found": [], "failures": {}, "complete": False,
        }
    finally:
        client.close()


def test_details_not_found_without_provider_failures(monkeypatch):
    monkeypatch.setattr(PubMedProvider, "enrich", lambda self, ids: [])
    client = _offline_client()
    try:
        result = DetailsService(client).fetch(["pmid:999999"])
        assert result["not_found"] == ["pmid:999999"]
        assert result["unresolved"] == []
        assert result["failures"] == {}
        assert result["complete"] is False
    finally:
        client.close()


def test_details_unresolved_during_provider_failure(monkeypatch):
    def failed(self, ids):
        raise RuntimeError("simulated upstream outage")

    monkeypatch.setattr(PubMedProvider, "enrich", failed)
    client = _offline_client()
    try:
        result = DetailsService(client).fetch(["pmid:999999"])
        assert result["not_found"] == []
        assert result["unresolved"] == ["pmid:999999"]
        assert "simulated upstream outage" in result["failures"]["pubmed:batch"]
        assert result["complete"] is False
    finally:
        client.close()
