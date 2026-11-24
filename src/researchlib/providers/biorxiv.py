from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..http import HTTPClient
from ..models import Author, DiscoveryPage, PaperRecord, SearchFilters
from .base import Provider
from .utils import normalize_doi, provenance, year_from


@dataclass
class PublicationLink:
    preprint_doi: str
    published_doi: str


class BioRxivProvider(Provider):
    name = "biorxiv"
    base = "https://api.biorxiv.org"

    def __init__(self, http: HTTPClient, server: str = "biorxiv", page_size: int = 100):
        if server not in {"biorxiv", "medrxiv"}:
            raise ValueError("server must be biorxiv or medrxiv")
        self.http, self.server = http, server
        self.name = server
        self.page_size = min(max(page_size, 1), 100)

    def discover(self, query: str, filters: SearchFilters, cursor: str | None = None) -> DiscoveryPage:
        if not filters.start_date or not filters.end_date:
            raise ValueError("bioRxiv/medRxiv tracking requires start_date and end_date")
        offset = int(cursor or 0)
        endpoint = f"{self.base}/details/{self.server}/{filters.start_date}/{filters.end_date}/{offset}"
        data = self.http.get(self.name, endpoint, min_interval=1.0).json()
        collection = data.get("collection") or []
        records = [self._parse(item, offset + index + 1) for index, item in enumerate(collection)]
        message = (data.get("messages") or [{}])[0]
        total = int(message.get("total", len(collection)) or 0)
        next_offset = offset + len(collection)
        next_cursor = str(next_offset) if collection and next_offset < total else None
        actual = f"{self.server}:{filters.start_date}/{filters.end_date}"
        return DiscoveryPage(records=records, next_cursor=next_cursor, done=next_cursor is None, actual_query=actual)

    def enrich(self, identifiers: list[dict[str, str]]) -> list[PaperRecord]:
        return []

    def publication_links(self, start_date: str, end_date: str, cursor: str | None = None) -> tuple[list[PublicationLink], str | None]:
        offset = int(cursor or 0)
        endpoint = f"{self.base}/pubs/{self.server}/{start_date}/{end_date}/{offset}"
        data = self.http.get(self.name, endpoint, min_interval=1.0).json()
        collection = data.get("collection") or []
        links = []
        for item in collection:
            preprint = normalize_doi(item.get("preprint_doi") or item.get("doi"))
            published = normalize_doi(item.get("published_doi") or item.get("published"))
            if preprint and published:
                links.append(PublicationLink(preprint, published))
        message = (data.get("messages") or [{}])[0]
        total = int(message.get("total", len(collection)) or 0)
        next_offset = offset + len(collection)
        return links, (str(next_offset) if collection and next_offset < total else None)

    def _parse(self, item: dict[str, Any], rank: int) -> PaperRecord:
        doi = normalize_doi(item.get("doi"))
        abstract = item.get("abstract") or None
        version = str(item.get("version") or "1")
        published_doi = normalize_doi(item.get("published"))
        authors = [Author(name=name.strip()) for name in (item.get("authors") or "").split(";") if name.strip()]
        return PaperRecord(
            title=item.get("title") or "[untitled]", authors=authors, year=year_from(item.get("date")),
            published_date=item.get("date"),
            journal=self.server, document_type="preprint", doi=doi, abstract=abstract,
            abstract_source=self.name if abstract else None,
            url=f"https://www.{self.server}.org/content/{doi}v{version}" if doi else None,
            publication_status="published" if published_doi else "preprint",
            preprint_version=version, published_doi=published_doi,
            source=self.name, source_rank=rank,
            field_sources=provenance(self.name, title=item.get("title"), authors=authors,
                                     year=item.get("date"), doi=doi, abstract=abstract),
            raw={"version": version, "date": item.get("date"), "published_doi": published_doi,
                 "category": item.get("category")},
        )
