"""Mocked OpenAlex contracts: direction, global top N and partial coverage."""
import httpx
import pytest

from researchlib.http import HTTPClient
from researchlib.related import RelatedPapersService


DOI = "10.1000/example/nested"


def work(number, count=0, doi=None):
    return {"id": f"https://openalex.org/W{number}", "title": f"Fixture work {number}",
            "doi": doi, "cited_by_count": count}


@pytest.fixture
def make_service(http_settings, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    clients = []

    def make(handler):
        http = HTTPClient(http_settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)
        clients.append(http)
        return RelatedPapersService(http)

    yield make
    for client in clients:
        client.close()


def test_directions_global_sort_top_n_and_complete_doi(make_service, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "test-secret")
    monkeypatch.setenv("CONTACT_EMAIL", "test@example.org")
    calls = []

    def handler(req):
        calls.append(req)
        assert req.url.params["api_key"] == "test-secret"
        assert req.url.params["mailto"] == "test@example.org"
        if req.url.path != "/works":
            assert req.url.path == f"/works/https://doi.org/{DOI}"
            return httpx.Response(200, json=work(1, 999, DOI))
        params = req.url.params
        assert params["sort"] == "cited_by_count:desc"
        assert params["per_page"] == "2" and params["page"] == "1"
        assert "cursor" not in params
        if params["filter"] == "cited_by:W1":
            return httpx.Response(200, json={"meta": {"count": 500}, "results": [work(2, 80), work(3, 20)]})
        assert params["filter"] == "cites:W1"
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work(4, 50)]})

    result = make_service(handler).fetch(f"https://doi.org/{DOI.upper()}", 2)
    assert result.status == "ok" and result.complete
    assert result.doi == DOI and len(calls) == 3
    assert [p.citation_count for p in result.references.results] == [80, 20]
    assert result.citations.results[0].openalex_id == "W4"
    assert result.references.total == 500
    assert result.references.raw_candidates == result.references.unique_candidates == 2
    assert result.references.display_truncated and result.references.truncated
    assert not result.references.candidates_truncated
    assert not result.citations.truncated
    assert result.references.results[0].field_sources["citation_count"] == "openalex"


@pytest.mark.parametrize("limit,expected", [(0, 1), (-5, 1), (101, 100)])
def test_limit_clamped_and_empty_graph_is_success(make_service, limit, expected):
    def handler(req):
        if req.url.path != "/works":
            return httpx.Response(200, json=work(1, doi=DOI))
        assert req.url.params["per_page"] == str(expected)
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})

    result = make_service(handler).fetch(DOI, limit)
    assert result.limit == expected and result.complete and result.status == "ok"
    assert result.references.total == result.citations.total == 0


def test_invalid_doi_does_not_request(make_service):
    def handler(req):
        pytest.fail("invalid DOI must not trigger a request")
    result = make_service(handler).fetch("not a DOI")
    assert result.status == "invalid" and not result.complete


@pytest.mark.parametrize("status,expected", [(404, "not_found"), (403, "error"), (503, "error")])
def test_target_missing_vs_upstream_error_and_secret_redaction(make_service, monkeypatch, status, expected):
    monkeypatch.setenv("OPENALEX_API_KEY", "do-not-expose")
    result = make_service(lambda req: httpx.Response(status)).fetch(DOI)
    assert result.status == expected and not result.complete
    assert bool(result.failures) == (expected == "error")
    assert "do-not-expose" not in result.model_dump_json()


@pytest.mark.parametrize("failed_relation", ["cited_by:W1", "cites:W1"])
def test_independent_branch_failure_keeps_other_results(make_service, failed_relation):
    def handler(req):
        if req.url.path != "/works":
            return httpx.Response(200, json=work(1, doi=DOI))
        if req.url.params["filter"] == failed_relation:
            return httpx.Response(403)
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work(2, 10)]})

    result = make_service(handler).fetch(DOI)
    failed, good = ((result.references, result.citations) if failed_relation.startswith("cited_by:")
                    else (result.citations, result.references))
    assert result.status == "partial" and not result.complete
    assert not failed.complete and failed.total is None and failed.candidates_truncated
    assert failed.failures and failed.truncated
    assert good.complete and len(good.results) == 1


@pytest.mark.parametrize("payload", [
    {"meta": {"count": 2}, "results": [work(2)]},
    {"meta": {"count": 2}, "results": [work(2, 1), work(2, 2)]},
    {"results": []},
    {"meta": {"count": -1}, "results": []},
    {"meta": {"count": 1}, "results": [{"title": "missing ID"}]},
])
def test_short_duplicate_and_malformed_responses_are_partial(make_service, payload):
    def handler(req):
        if req.url.path != "/works":
            return httpx.Response(200, json=work(1, doi=DOI))
        return httpx.Response(200, json=payload)
    result = make_service(handler).fetch(DOI, 2)
    assert result.status == "partial" and not result.complete
    assert result.references.candidates_truncated and result.references.failures


def test_sort_zero_and_unknown_counts_and_deduplicate(make_service):
    def handler(req):
        if req.url.path != "/works":
            return httpx.Response(200, json=work(1, doi=DOI))
        return httpx.Response(200, json={"meta": {"count": 4}, "results": [
            work(2, None), work(3, None), work(3, 0), work(4, 15), work(5, 15), work(4, 10),
        ]})
    result = make_service(handler).fetch(DOI)
    assert result.complete
    assert [p.openalex_id for p in result.references.results] == ["W4", "W5", "W3", "W2"]
    assert result.references.raw_candidates == 6 and result.references.unique_candidates == 4
    assert result.references.results[-2].citation_count == 0
    assert result.references.results[-1].citation_count is None


def test_wrong_target_identity_is_error(make_service):
    result = make_service(lambda req: httpx.Response(200, json=work(1, doi="10.1000/other"))).fetch(DOI)
    assert result.status == "error" and result.failures and result.paper is None


def test_request_budget_failure_preserves_finished_branch(make_service):
    def handler(req):
        if req.url.path != "/works":
            return httpx.Response(200, json=work(1, doi=DOI))
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [work(2, 5)]})
    service = make_service(handler)
    service.provider.http.settings.request_budget_per_source = 2
    result = service.fetch(DOI)
    assert result.status == "partial"
    assert result.references.complete and result.references.results
    assert "RequestBudgetExceeded" in result.failures["openalex:citations"]
    assert result.citations.candidates_truncated and result.citations.total is None


def test_doi_reserved_url_characters_are_encoded(make_service):
    doi = "10.1000/test?query#fragment"
    def handler(req):
        if req.url.path != "/works":
            assert req.url.path == f"/works/https://doi.org/{doi}"
            assert not req.url.fragment
            assert "query" not in req.url.params
            return httpx.Response(200, json=work(1, doi=doi))
        return httpx.Response(200, json={"meta": {"count": 0}, "results": []})
    assert make_service(handler).fetch(doi).status == "ok"
