import httpx

from researchlib.config import HTTPSettings, Settings
from researchlib.details import DetailsService
from researchlib.http import HTTPClient, RequestBudgetExceeded
from researchlib.merge import merge_records
from researchlib.models import Author, DiscoveryPage, PaperRecord, SearchFilters
from researchlib.providers.arxiv import ArxivProvider
from researchlib.providers.openalex import OpenAlexProvider
from researchlib.providers.pubmed import PubMedProvider
from researchlib.query import arxiv_query, europe_pmc_query, plain_query
from researchlib.ranking import query_terms, rank_records
from researchlib.service import SearchService
from researchlib.providers.base import Provider, ProviderRegistry


def test_transitive_merge_join_doi_pmid():
    records = [
        PaperRecord(title='Model A', doi='10.1000/x', source='a'),
        PaperRecord(title='Model A', pmid='1234', source='b'),
        PaperRecord(title='Model A', doi='10.1000/x', pmid='1234', source='c'),
    ]
    groups = merge_records(records)
    assert len(groups) == 1
    assert groups[0].sources == ['a', 'b', 'c']


def test_conflicting_identifiers_do_not_merge():
    records = [
        PaperRecord(title='Same title', doi='10.1000/a', pmid='1234', source='a'),
        PaperRecord(title='Same title', doi='10.1000/b', pmid='1234', source='b'),
    ]
    assert len(merge_records(records)) == 2


def test_rank_does_not_reward_excluded_terms():
    terms = query_terms(['microprotein NOT ("small cell lung cancer")'])
    assert terms == ['microprotein']
    papers = merge_records([
        PaperRecord(title='microprotein study', source='a'),
        PaperRecord(title='lung cancer', source='b'),
    ])
    ranked = rank_records(papers, ['microprotein NOT ("small cell lung cancer")'])
    assert ranked[0].paper.title == 'microprotein study'
    assert ranked[1].keyword_score == 0


def test_cross_provider_query_adapters():
    query = '(microprotein OR "small ORF") AND (coding potential) NOT ("lung cancer")'
    assert 'lung' not in plain_query(query)
    assert 'ANDNOT' in arxiv_query(query)
    assert 'all:"small ORF"' in arxiv_query(query)
    assert 'AND NOT' in europe_pmc_query(query)


def test_normalize_doi_rejects_random_text():
    from researchlib.providers.utils import normalize_doi
    assert normalize_doi('hello world') is None
    assert normalize_doi('https://doi.org/10.1234/ABC.') == '10.1234/abc'


def test_pubmed_year_filters_and_relevance_sort():
    seen = []
    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={'esearchresult': {'idlist': [], 'count': '0'}}, request=req)
    http = HTTPClient(HTTPSettings(), transport=httpx.MockTransport(handler))
    PubMedProvider(http).discover('microprotein', SearchFilters(from_year=2020, to_year=2025))
    assert seen[0]['mindate'] == '2020-01-01'
    assert seen[0]['maxdate'] == '2025-12-31'
    assert seen[0]['sort'] == 'relevance'
    http.close()


def test_openalex_paging_without_api_key(monkeypatch):
    monkeypatch.delenv('OPENALEX_API_KEY', raising=False)
    seen = []
    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, json={
            'results': [{'title': 'A', 'id': 'https://openalex.org/W42'}],
            'meta': {'count': 75, 'next_cursor': 'key-required'},
        }, request=req)
    http = HTTPClient(HTTPSettings(), transport=httpx.MockTransport(handler))
    provider = OpenAlexProvider(http, per_page=50)
    first = provider.discover('microprotein', SearchFilters())
    assert first.next_cursor == 'p:2'
    provider.discover('microprotein', SearchFilters(), cursor='p:2')
    assert seen[1]['page'] == '2'
    assert 'cursor' not in seen[1]
    http.close()


def test_openalex_semantic_filter_uses_supported_publication_year(monkeypatch):
    monkeypatch.delenv('OPENALEX_API_KEY', raising=False)
    params = []
    def handler(req):
        params.append(dict(req.url.params))
        return httpx.Response(200, json={'results': [], 'meta': {}}, request=req)
    http = HTTPClient(HTTPSettings(), transport=httpx.MockTransport(handler))
    OpenAlexProvider(http).discover('microprotein', SearchFilters(semantic=True, from_year=2020, to_year=2025))
    assert params[0]['filter'] == 'publication_year:>2019,publication_year:<2026'
    assert 'search.semantic' in params[0]
    http.close()


class CapturingProvider(Provider):
    name = 'mock'
    per_page = 3
    def discover(self, query, filters, cursor=None):
        return DiscoveryPage(records=[
            PaperRecord(title=f'{query} out of range', year=2010, source='mock'),
            PaperRecord(title=f'{query} in range', year=2023, source='mock'),
        ], done=True, actual_query=query)
    def enrich(self, identifiers):
        return []


def test_local_year_filter_and_query_rank_positions():
    registry = ProviderRegistry()
    registry.register(CapturingProvider())
    result = SearchService(registry, Settings()).search(['alpha', 'beta'], filters=SearchFilters(from_year=2020), candidate_limit=5)
    assert result.raw_candidates == 2
    assert all(hit.paper.year == 2023 for hit in result.results)
    assert all(hit.score >= 0 for hit in result.results)


def test_details_identifier_validation():
    svc = DetailsService(HTTPClient(HTTPSettings()))
    assert svc.classify('garbage') is None
    assert svc.classify('pmid:123') == ('pmid', '123')
    assert svc.classify('openalex:W123') == ('openalex', 'W123')
    assert svc.classify('arxiv:2601.00001v2') == ('arxiv', '2601.00001')
    assert svc.fetch([])['error'].startswith('provide 1')
    svc.http.close()


def test_http_request_budget_rolls_over():
    fake_time = [100.0]
    count = [0]
    def handler(req):
        count[0] += 1
        return httpx.Response(200, json={'ok': count[0]}, request=req)
    settings = HTTPSettings(request_budget_per_source=1, request_budget_window_seconds=60)
    http = HTTPClient(settings, transport=httpx.MockTransport(handler), monotonic=lambda: fake_time[0])
    http.get('s', 'https://example.test/a', cache=False)
    try:
        http.get('s', 'https://example.test/b', cache=False)
    except RequestBudgetExceeded:
        pass
    else:
        assert False, 'request budget must stop during the active window'
    fake_time[0] += 61
    http.get('s', 'https://example.test/b', cache=False)
    assert count[0] == 2
    http.close()


def test_arxiv_topic_query_uses_relevance_order():
    seen = []
    def handler(req):
        seen.append(dict(req.url.params))
        return httpx.Response(200, content=b'<feed xmlns="http://www.w3.org/2005/Atom"/>', request=req)
    http = HTTPClient(HTTPSettings(), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    ArxivProvider(http).discover('microprotein', SearchFilters())
    assert seen[0]['sortBy'] == 'relevance'
    http.close()


def test_or_expansion_is_not_flattened_to_overconstrained_query():
    from researchlib.query import plain_variants
    variants = plain_variants('(microprotein OR "small ORF" OR sORF) AND ("coding potential" OR translation) NOT ("lung cancer")')
    assert len(variants) == 4
    assert variants[0] == 'microprotein coding potential'
    assert variants[1] == 'small ORF translation'
    assert all('lung' not in variant for variant in variants)
