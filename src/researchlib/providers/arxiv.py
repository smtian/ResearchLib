from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ..http import HTTPClient
from ..models import Author, DiscoveryPage, PaperRecord, SearchFilters
from ..query import arxiv_query
from .base import Provider
from .utils import clean_text, normalize_doi, provenance, year_from

ATOM = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivProvider(Provider):
    name = "arxiv"
    url = "https://export.arxiv.org/api/query"

    def __init__(self, http: HTTPClient, page_size: int = 50, min_interval: float = 3.0):
        self.http = http
        self.page_size = min(max(page_size, 1), 2000)
        self.min_interval = max(min_interval, 3.0)

    def discover(self, query: str, filters: SearchFilters, cursor: str | None = None) -> DiscoveryPage:
        actual = arxiv_query(query)
        start_date = filters.start_date or (f"{filters.from_year}-01-01" if filters.from_year else None)
        end_date = filters.end_date or (f"{filters.to_year}-12-31" if filters.to_year else None)
        if start_date or end_date:
            start = (start_date or "1900-01-01").replace("-", "") + "0000"
            end = (end_date or "2999-12-31").replace("-", "") + "2359"
            actual = f"({actual}) AND submittedDate:[{start} TO {end}]"
        start_index = int(cursor or 0)
        response = self.http.get(
            self.name, self.url,
            params={"search_query": actual, "start": start_index, "max_results": self.page_size,
                    "sortBy": "submittedDate" if (filters.start_date or filters.end_date) else "relevance",
                    "sortOrder": "descending"},
            min_interval=self.min_interval,
        )
        records = self._parse(response.content, start_index)
        next_cursor = str(start_index + len(records)) if len(records) == self.page_size else None
        return DiscoveryPage(records=records, next_cursor=next_cursor, done=next_cursor is None, actual_query=actual)

    def enrich(self, identifiers: list[dict[str, str]]) -> list[PaperRecord]:
        ids = [item["arxiv"] for item in identifiers if item.get("arxiv")]
        if not ids:
            return []
        response = self.http.get(
            self.name, self.url, params={"id_list": ",".join(ids), "max_results": len(ids)},
            min_interval=self.min_interval,
        )
        return self._parse(response.content, 0)

    def _parse(self, content: bytes, offset: int) -> list[PaperRecord]:
        root = ET.fromstring(content)
        records = []
        for rank, entry in enumerate(root.findall("atom:entry", ATOM), start=offset + 1):
            identifier_url = (entry.findtext("atom:id", default="", namespaces=ATOM)).strip()
            identifier = identifier_url.rsplit("/", 1)[-1]
            match = re.match(r"(.+?)(?:v(\d+))?$", identifier)
            arxiv_id = match.group(1) if match else identifier
            version = match.group(2) if match and match.group(2) else "1"
            title = clean_text(entry.findtext("atom:title", namespaces=ATOM)) or "[untitled]"
            abstract = clean_text(entry.findtext("atom:summary", namespaces=ATOM))
            published = entry.findtext("atom:published", namespaces=ATOM)
            updated = entry.findtext("atom:updated", namespaces=ATOM)
            doi = normalize_doi(entry.findtext("arxiv:doi", namespaces=ATOM))
            authors = [
                Author(name=name)
                for author in entry.findall("atom:author", ATOM)
                if (name := clean_text(author.findtext("atom:name", namespaces=ATOM)))
            ]
            records.append(PaperRecord(
                title=title, authors=authors, year=year_from(published),
                published_date=published[:10] if published else None, document_type="preprint",
                doi=None, arxiv_id=arxiv_id, abstract=abstract,
                abstract_source=self.name if abstract else None, url=identifier_url or None,
                publication_status="preprint", source=self.name, source_rank=rank,
                preprint_version=version, published_doi=doi,
                field_sources=provenance(self.name, title=title, authors=authors, year=published,
                                         arxiv_id=arxiv_id, abstract=abstract),
                raw={"version": version, "published": published, "updated": updated},
            ))
        return records

