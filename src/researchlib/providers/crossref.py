from __future__ import annotations

import os

import httpx
from typing import Any
from urllib.parse import quote

from ..http import HTTPClient
from ..models import Author, DiscoveryPage, PaperRecord, SearchFilters
from ..query import plain_query
from .base import Provider
from .utils import clean_text, normalize_doi, provenance


class CrossrefProvider(Provider):
    name = "crossref"
    url = "https://api.crossref.org/works"

    def __init__(self, http: HTTPClient, per_page: int = 50):
        self.http, self.per_page = http, min(per_page, 1000)

    def _headers(self) -> dict[str, str]:
        email = os.getenv("CONTACT_EMAIL")
        agent = "researchlib/0.1"
        if email:
            agent += f" (mailto:{email})"
        return {"User-Agent": agent}

    def discover(self, query: str, filters: SearchFilters, cursor: str | None = None) -> DiscoveryPage:
        params: dict[str, Any] = {"query.bibliographic": plain_query(query), "rows": self.per_page, "cursor": cursor or "*", "select": "DOI,title,author,published,container-title,type,abstract,URL"}
        clauses = []
        if filters.from_year:
            clauses.append(f"from-pub-date:{filters.from_year}-01-01")
        if filters.to_year:
            clauses.append(f"until-pub-date:{filters.to_year}-12-31")
        if clauses:
            params["filter"] = ",".join(clauses)
        message = self.http.get(self.name, self.url, params=params, headers=self._headers(), min_interval=0.05).json()["message"]
        items = message.get("items", [])
        records = [self._parse(item, i + 1) for i, item in enumerate(items)]
        next_cursor = message.get("next-cursor")
        return DiscoveryPage(records=records, next_cursor=next_cursor, done=not records or not next_cursor or next_cursor == cursor, actual_query=query)

    def enrich(self, identifiers: list[dict[str, str]]) -> list[PaperRecord]:
        records = []
        for ids in identifiers:
            if not ids.get("doi"):
                continue
            try:
                message = self.http.get(self.name, f"{self.url}/{quote(ids['doi'], safe='')}", headers=self._headers(), min_interval=0.05).json()["message"]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
            records.append(self._parse(message, None))
        return records

    def track_venue(
        self,
        issn: str,
        start_date: str,
        end_date: str,
        *,
        cursor: str | None = None,
        from_index_date: str | None = None,
        rows: int | None = None,
    ) -> DiscoveryPage:
        clauses = [f"from-pub-date:{start_date}", f"until-pub-date:{end_date}"]
        if from_index_date:
            clauses.append(f"from-index-date:{from_index_date}")
        endpoint = f"https://api.crossref.org/journals/{quote(issn, safe='')}/works"
        params = {
            "rows": min(rows or self.per_page, 1000), "cursor": cursor or "*",
            "filter": ",".join(clauses),
            "select": "DOI,title,author,published,published-print,published-online,container-title,type,abstract,URL,ISSN,indexed",
        }
        message = self.http.get(
            self.name, endpoint, params=params, headers=self._headers(), min_interval=0.05
        ).json()["message"]
        items = message.get("items", [])
        records = [self._parse(item, index + 1) for index, item in enumerate(items)]
        next_cursor = message.get("next-cursor")
        actual = f"ISSN:{issn} FILTER:{','.join(clauses)}"
        return DiscoveryPage(
            records=records, next_cursor=next_cursor,
            done=not records or not next_cursor or next_cursor == cursor,
            actual_query=actual,
        )

    @staticmethod
    def _published_date(item: dict[str, Any]) -> str | None:
        for key in ("published-online", "published-print", "published", "issued"):
            parts = (item.get(key) or {}).get("date-parts") or []
            if parts and parts[0]:
                return "-".join(f"{int(part):0{4 if index == 0 else 2}d}" for index, part in enumerate(parts[0][:3]))
        return None

    @staticmethod
    def _date_year(item: dict[str, Any]) -> int | None:
        for key in ("published-print", "published-online", "published", "issued"):
            parts = item.get(key, {}).get("date-parts", [])
            if parts and parts[0]:
                return parts[0][0]
        return None

    def _parse(self, item: dict[str, Any], rank: int | None) -> PaperRecord:
        authors = []
        for author in item.get("author") or []:
            name = author.get("name") or " ".join(filter(None, [author.get("given"), author.get("family")]))
            if name:
                authors.append(Author(name=name, orcid=author.get("ORCID")))
        titles = item.get("title") or []
        containers = item.get("container-title") or []
        abstract = clean_text(item.get("abstract"))
        return PaperRecord(
            title=titles[0] if titles else "[untitled]", authors=authors, year=self._date_year(item),
            published_date=self._published_date(item),
            journal=containers[0] if containers else None, document_type=item.get("type"), doi=normalize_doi(item.get("DOI")),
            abstract=abstract, abstract_source=self.name if abstract else None, url=item.get("URL"),
            publication_status="published" if self._date_year(item) else None,
            source=self.name, source_rank=rank,
            field_sources=provenance(self.name, title=titles[0] if titles else None, authors=authors,
                                     year=self._date_year(item), journal=containers[0] if containers else None,
                                     doi=item.get("DOI"), abstract=abstract), raw=item,
        )
