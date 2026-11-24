from __future__ import annotations

from typing import Any

from ..http import HTTPClient
from ..models import DiscoveryPage, PaperRecord, SearchFilters
from ..query import europe_pmc_query
from .base import Provider
from ..models import Author
from .utils import normalize_doi, provenance, year_from


class EuropePMCProvider(Provider):
    name = "europe_pmc"
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def __init__(self, http: HTTPClient, per_page: int = 50):
        self.http, self.per_page = http, min(per_page, 1000)

    def discover(self, query: str, filters: SearchFilters, cursor: str | None = None) -> DiscoveryPage:
        actual = europe_pmc_query(query)
        if filters.from_year or filters.to_year:
            actual += f" AND FIRST_PDATE:[{filters.from_year or 1000}-01-01 TO {filters.to_year or 3000}-12-31]"
        data = self.http.get(self.name, self.url, params={
            "query": actual, "format": "json", "resultType": "core", "pageSize": self.per_page,
            "cursorMark": cursor or "*",
        }).json()
        items = data.get("resultList", {}).get("result", [])
        records = [self._parse(item, i + 1) for i, item in enumerate(items)]
        next_cursor = data.get("nextCursorMark")
        return DiscoveryPage(records=records, next_cursor=next_cursor, done=not records or not next_cursor or next_cursor == cursor, actual_query=actual)

    def enrich(self, identifiers: list[dict[str, str]]) -> list[PaperRecord]:
        records = []
        for ids in identifiers:
            if ids.get("pmid"):
                query = f"EXT_ID:{ids['pmid']} AND SRC:MED"
            elif ids.get("doi"):
                query = f'DOI:"{ids["doi"]}"'
            else:
                continue
            records.extend(self.discover(query, SearchFilters()).records)
        return records

    def _parse(self, item: dict[str, Any], rank: int | None) -> PaperRecord:
        author_list = item.get("authorList", {}).get("author", [])
        authors = []
        for author in author_list:
            name = author.get("fullName") or author.get("collectiveName")
            author_id = author.get("authorId")
            orcid = author_id.get("value") if isinstance(author_id, dict) else author_id
            if name:
                authors.append(Author(name=name, orcid=orcid))
        abstract = item.get("abstractText")
        source_name = item.get("journalInfo", {}).get("journal", {}).get("title") or item.get("source")
        fulltext_urls = item.get("fullTextUrlList", {}).get("fullTextUrl", [])
        oa_url = next((entry.get("url") for entry in fulltext_urls if entry.get("url")), None) if item.get("isOpenAccess") == "Y" else None
        return PaperRecord(
            title=item.get("title") or "[untitled]", authors=authors, year=year_from(item.get("firstPublicationDate") or item.get("pubYear")),
            journal=source_name, published_date=item.get("firstPublicationDate"),
            document_type=item.get("pubType"), doi=normalize_doi(item.get("doi")),
            pmid=str(item.get("pmid")) if item.get("pmid") else None, pmcid=item.get("pmcid"),
            abstract=abstract, abstract_source=self.name if abstract else None,
            url=f"https://europepmc.org/article/{item.get('source')}/{item.get('id')}" if item.get("id") else None,
            oa_url=oa_url, publication_status="published" if item.get("firstPublicationDate") else None,
            source=self.name, source_rank=rank,
            field_sources=provenance(self.name, title=item.get("title"), authors=authors, year=item.get("pubYear"),
                                     journal=source_name, doi=item.get("doi"), pmid=item.get("pmid"),
                                     pmcid=item.get("pmcid"), abstract=abstract, oa_url=oa_url), raw=item,
        )
