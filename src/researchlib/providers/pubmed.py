from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any

from ..http import HTTPClient
from ..models import Author, DiscoveryPage, PaperRecord, SearchFilters
from .base import Provider
from .utils import normalize_doi, provenance, year_from


class PubMedProvider(Provider):
    name = "pubmed"
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, http: HTTPClient, per_page: int = 50):
        self.http, self.per_page = http, min(per_page, 200)

    def _common(self) -> dict[str, str]:
        values = {"tool": "researchlib", "email": os.getenv("CONTACT_EMAIL", "")}
        key = os.getenv("NCBI_API_KEY")
        if key:
            values["api_key"] = key
        return values

    def _interval(self) -> float:
        return 0.1 if os.getenv("NCBI_API_KEY") else 0.34

    def discover(self, query: str, filters: SearchFilters, cursor: str | None = None) -> DiscoveryPage:
        terms = [query]
        terms.extend(f'"{mesh}"[MeSH Terms]' for mesh in filters.mesh)
        actual = " AND ".join(f"({term})" for term in terms)
        params: dict[str, Any] = {**self._common(), "db": "pubmed", "term": actual, "retmode": "json", "sort": "relevance", "retmax": self.per_page, "retstart": int(cursor or 0)}
        start_date = filters.start_date or (f"{filters.from_year}-01-01" if filters.from_year else None)
        end_date = filters.end_date or (f"{filters.to_year}-12-31" if filters.to_year else None)
        if start_date or end_date:
            params.update({"datetype": "pdat", "mindate": start_date, "maxdate": end_date})
        search = self.http.get(self.name, f"{self.base}/esearch.fcgi", params=params, min_interval=self._interval()).json()["esearchresult"]
        ids = search.get("idlist", [])
        records = []
        if ids:
            fetch_params = {**self._common(), "db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
            response = self.http.get(self.name, f"{self.base}/efetch.fcgi", params=fetch_params, min_interval=self._interval())
            records = self._parse_xml(response.content, int(cursor or 0))
        offset = int(cursor or 0) + len(ids)
        count = int(search.get("count", 0))
        next_cursor = str(offset) if offset < count and ids else None
        return DiscoveryPage(records=records, next_cursor=next_cursor, done=next_cursor is None, actual_query=actual, request_count=2 if ids else 1)

    def enrich(self, identifiers: list[dict[str, str]]) -> list[PaperRecord]:
        ids = [item["pmid"] for item in identifiers if item.get("pmid")]
        if not ids:
            return []
        params = {**self._common(), "db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
        response = self.http.get(self.name, f"{self.base}/efetch.fcgi", params=params, min_interval=self._interval())
        return self._parse_xml(response.content, 0)

    @staticmethod
    def _text(element: ET.Element | None) -> str | None:
        if element is None:
            return None
        value = "".join(element.itertext()).strip()
        return value or None

    def _parse_xml(self, content: bytes, offset: int) -> list[PaperRecord]:
        root = ET.fromstring(content)
        records = []
        for index, article in enumerate(root.findall(".//PubmedArticle"), start=1):
            citation = article.find("MedlineCitation")
            node = citation.find("Article") if citation is not None else None
            if node is None:
                continue
            pmid = self._text(citation.find("PMID"))
            identifiers = {}
            for aid in article.findall(".//ArticleIdList/ArticleId"):
                identifiers[aid.attrib.get("IdType", "").lower()] = self._text(aid)
            abstract_parts = []
            for part in node.findall(".//Abstract/AbstractText"):
                text = self._text(part)
                if text:
                    label = part.attrib.get("Label")
                    abstract_parts.append(f"{label}: {text}" if label else text)
            author_values = []
            for author in node.findall(".//AuthorList/Author"):
                collective = self._text(author.find("CollectiveName"))
                name = collective or " ".join(filter(None, [self._text(author.find("ForeName")), self._text(author.find("LastName"))]))
                orcid_node = author.find("Identifier[@Source='ORCID']")
                if name:
                    author_values.append(Author(name=name, orcid=self._text(orcid_node)))
            journal = self._text(node.find(".//Journal/Title"))
            year = year_from(self._text(node.find(".//JournalIssue/PubDate/Year")) or self._text(node.find(".//JournalIssue/PubDate/MedlineDate")))
            date_node = node.find(".//JournalIssue/PubDate")
            month = self._text(date_node.find("Month")) if date_node is not None else None
            day = self._text(date_node.find("Day")) if date_node is not None else None
            published_date = f"{year:04d}" if year else None
            if published_date and month and month.isdigit():
                published_date += f"-{int(month):02d}"
                if day and day.isdigit():
                    published_date += f"-{int(day):02d}"
            pub_types = [self._text(p) for p in node.findall(".//PublicationTypeList/PublicationType")]
            abstract = "\n".join(abstract_parts) or None
            records.append(PaperRecord(
                title=self._text(node.find("ArticleTitle")) or "[untitled]", authors=author_values, year=year,
                published_date=published_date, journal=journal,
                document_type=next((x for x in pub_types if x), None), doi=normalize_doi(identifiers.get("doi")),
                pmid=pmid, pmcid=identifiers.get("pmc"), abstract=abstract, abstract_source=self.name if abstract else None,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                publication_status="published", source=self.name, source_rank=offset + index,
                field_sources=provenance(self.name, title=self._text(node.find("ArticleTitle")), authors=author_values,
                                         year=year, journal=journal, doi=identifiers.get("doi"), pmid=pmid,
                                         pmcid=identifiers.get("pmc"), abstract=abstract),
                raw={"identifiers": identifiers},
            ))
        return records

