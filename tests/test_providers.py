import gzip
import json

import httpx

from researchlib.http import HTTPClient
from researchlib.models import SearchFilters
from researchlib.providers.arxiv import ArxivProvider
from researchlib.providers.biorxiv import BioRxivProvider
from researchlib.providers.crossref import CrossrefProvider
from researchlib.providers.openalex import OpenAlexProvider, restore_abstract
from researchlib.providers.pubmed import PubMedProvider


def client(settings, handler):
    return HTTPClient(settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_openalex_abstract_and_cursor(http_settings):
    assert restore_abstract({"world": [1], "hello": [0]}) == "hello world"
    def handler(request):
        return httpx.Response(200, json={
            "results": [{"id": "https://openalex.org/W1", "title": "T", "abstract_inverted_index": {"A": [0]}}],
            "meta": {"next_cursor": "next"},
        }, request=request)
    page = OpenAlexProvider(client(http_settings, handler)).discover("protein", SearchFilters())
    assert page.records[0].abstract == "A" and page.next_cursor == "next"


def test_pubmed_multisection_xml(http_settings):
    xml = b'''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID><Article>
    <ArticleTitle>Study</ArticleTitle><Abstract><AbstractText Label="A">One</AbstractText><AbstractText Label="B">Two</AbstractText></Abstract>
    </Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType="doi">10.1/X</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'''
    def handler(request):
        if request.url.path.endswith("esearch.fcgi"):
            return httpx.Response(200, json={"esearchresult": {"idlist": ["42"], "count": "1"}}, request=request)
        return httpx.Response(200, content=xml, request=request)
    record = PubMedProvider(client(http_settings, handler)).discover("x", SearchFilters()).records[0]
    assert record.doi == "10.1/x" and record.abstract == "A: One\nB: Two"


def test_crossref_venue_incremental_filter(http_settings):
    def handler(request):
        assert "from-index-date:2026-11-10" in request.url.params["filter"]
        return httpx.Response(200, json={"message": {"items": [], "next-cursor": None}}, request=request)
    page = CrossrefProvider(client(http_settings, handler)).track_venue(
        "1367-4811", "2026-11-01", "2026-11-12", from_index_date="2026-11-10"
    )
    assert page.done


def test_arxiv_version_and_published_doi(http_settings):
    atom = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry>
    <id>http://arxiv.org/abs/2601.00001v2</id><published>2026-01-01T00:00:00Z</published><title>T</title><summary>A</summary>
    <arxiv:doi>10.1/final</arxiv:doi></entry></feed>'''
    provider = ArxivProvider(client(http_settings, lambda req: httpx.Response(200, content=atom, request=req)), page_size=1)
    record = provider.discover("all:protein", SearchFilters()).records[0]
    assert record.arxiv_id == "2601.00001" and record.preprint_version == "2"
    assert record.published_doi == "10.1/final" and record.doi is None


def test_biorxiv_publication_link(http_settings):
    def handler(request):
        body = ({"messages": [{"total": "1"}], "collection": [{"preprint_doi": "10.1101/x", "published_doi": "10.1/y"}]}
                if "/pubs/" in request.url.path else
                {"messages": [{"total": "1"}], "collection": [{"doi": "10.1101/x", "version": "2", "title": "T"}]})
        return httpx.Response(200, json=body, request=request)
    provider = BioRxivProvider(client(http_settings, handler))
    record = provider.discover("", SearchFilters(start_date="2026-01-01", end_date="2026-01-01")).records[0]
    links, _ = provider.publication_links("2026-01-01", "2026-01-01")
    assert record.preprint_version == "2" and links[0].published_doi == "10.1/y"



def test_crossref_doi_lookup_twice_with_gzip_cache(http_settings):
    """Regression for lookup_doi failing on a cached gzipped Crossref reply."""
    payload = {"message": {
        "DOI": "10.64898/2026.09.29.755517",
        "title": ["Example DOI response"],
        "author": [],
        "published": {"date-parts": [[2026, 9, 29]]},
    }}
    raw = gzip.compress(json.dumps(payload).encode())
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=raw, headers={
            "Content-Type": "application/json", "Content-Encoding": "gzip",
        }, request=request)

    http = client(http_settings, handler)
    provider = CrossrefProvider(http, 1)
    ids = [{"doi": "10.64898/2026.09.29.755517"}]
    try:
        first = provider.enrich(ids)
        second = provider.enrich(ids)
        assert first[0].doi == second[0].doi == "10.64898/2026.09.29.755517"
        assert calls == 1
    finally:
        http.close()
