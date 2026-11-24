from researchlib.config import Settings
from researchlib.models import DiscoveryPage, PaperRecord, SearchFilters
from researchlib.providers.base import Provider, ProviderRegistry
from researchlib.service import SearchService


class FakeProvider(Provider):
    def __init__(self, name, records):
        self.name, self.records, self.per_page = name, records, 50
    def discover(self, query, filters, cursor=None):
        return DiscoveryPage(records=[record.model_copy(deep=True) for record in self.records], done=True, actual_query=query)
    def enrich(self, identifiers):
        return []


class FailedProvider(FakeProvider):
    def discover(self, query, filters, cursor=None):
        raise RuntimeError("temporary")


def test_search_merges_doi_ranks_and_isolates_failure():
    registry = ProviderRegistry()
    registry.register(FakeProvider("a", [PaperRecord(title="Protein model", doi="10.1/X", source="a", abstract="A")]))
    registry.register(FakeProvider("b", [PaperRecord(title="Protein model", doi="10.1/x", source="b", pmid="1")]))
    registry.register(FailedProvider("failed", []))
    result = SearchService(registry, Settings()).search(["protein model"], result_limit=10, candidate_limit=30)
    assert result.raw_candidates == 2 and result.unique_candidates == 1
    assert result.results[0].sources == ["a", "b"]
    assert "failed:q1" in result.failures


def test_search_never_uses_citation_count_as_relevance():
    registry = ProviderRegistry()
    registry.register(FakeProvider("a", [
        PaperRecord(title="Unrelated", citation_count=100000, source="a"),
        PaperRecord(title="Exact microprotein match", citation_count=0, source="a"),
    ]))
    result = SearchService(registry, Settings()).search(["microprotein"], result_limit=2, candidate_limit=10)
    assert result.results[0].paper.title == "Exact microprotein match"

