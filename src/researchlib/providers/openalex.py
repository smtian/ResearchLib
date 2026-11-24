from __future__ import annotations

import os
from urllib.parse import quote

import httpx
from typing import Any, Literal

from ..http import HTTPClient
from ..models import DiscoveryPage, PaperRecord, SearchFilters
from ..query import plain_query
from .base import Provider
from .utils import author_names, normalize_doi, provenance


def restore_abstract(index: dict[str, list[int]] | None) -> str | None:
    if not index:
        return None
    positions = [(position, word) for word, indexes in index.items() for position in indexes]
    if not positions:
        return None
    positions.sort()
    return " ".join(word for _, word in positions)


class OpenAlexProvider(Provider):
    name = "openalex"
    url = "https://api.openalex.org/works"

    def __init__(self, http: HTTPClient, per_page: int = 50, semantic_search: bool = False):
        self.http, self.per_page, self.semantic_search = http, min(per_page, 100), semantic_search

    def discover(self, query: str, filters: SearchFilters, cursor: str | None = None) -> DiscoveryPage:
        semantic = filters.semantic
        per_page = min(self.per_page, 50) if semantic else self.per_page
        params: dict[str, Any] = {
            "search.semantic" if semantic else "search": plain_query(query),
            "per-page": per_page,
            "select": "id,doi,title,display_name,authorships,publication_year,primary_location,type,abstract_inverted_index,cited_by_count,open_access,ids,publication_date",
        }
        key = os.getenv("OPENALEX_API_KEY")
        if not semantic:
            if key:
                params["cursor"] = cursor or "*"
            else:
                params["page"] = int(cursor.removeprefix("p:")) if cursor else 1
        clauses = []
        if filters.from_year:
            clauses.append(f"from_publication_date:{filters.from_year}-01-01")
        if filters.to_year:
            clauses.append(f"to_publication_date:{filters.to_year}-12-31")
        if semantic:
            clauses = []
            if filters.from_year:
                clauses.append(f"publication_year:>{filters.from_year - 1}")
            if filters.to_year:
                clauses.append(f"publication_year:<{filters.to_year + 1}")
        if clauses:
            params["filter"] = ",".join(clauses)
        email = os.getenv("CONTACT_EMAIL")
        if email:
            params["mailto"] = email
        if key:
            params["api_key"] = key
        data = self.http.get(self.name, self.url, params=params, min_interval=1.0 if semantic else 0).json()
        records = [self._parse(item, i + 1) for i, item in enumerate(data.get("results", []))]
        meta = data.get("meta") or {}
        if semantic:
            next_cursor = None  # semantic API has at most one 50-result page
        elif key:
            next_cursor = meta.get("next_cursor") if records else None
        else:
            page_no = int(params["page"])
            total = int(meta.get("count", 0) or 0)
            next_cursor = (
                f"p:{page_no + 1}" if records and page_no * per_page < min(total, 10000)
                else meta.get("next_cursor") if "count" not in meta else None
            )
        return DiscoveryPage(records=records, next_cursor=next_cursor, done=not next_cursor, actual_query=params.get("search.semantic") or params.get("search", query))

    def enrich(self, identifiers: list[dict[str, str]]) -> list[PaperRecord]:
        records = []
        for ids in identifiers:
            identifier = ids.get("openalex") or ids.get("doi")
            if not identifier:
                continue
            suffix = identifier.rsplit("/", 1)[-1]
            try:
                item = self.http.get(self.name, f"{self.url}/{suffix}").json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
            records.append(self._parse(item, None))
        return records

    @staticmethod
    def _request_params() -> dict[str, str]:
        return {key: value for key, env in (("api_key", "OPENALEX_API_KEY"),
                                            ("mailto", "CONTACT_EMAIL"))
                if (value := os.getenv(env))}

    def resolve_doi(self, doi: str) -> PaperRecord | None:
        """Use the complete DOI, including its prefix and any nested slashes."""
        try:
            item = self.http.get(
                self.name, f"{self.url}/https://doi.org/{quote(doi, safe='')}",
                params=self._request_params(), min_interval=0.1,
            ).json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return self._parse(item, None)

    def related_works(
        self, work_id: str, relation: Literal["references", "citations"], limit: int,
    ) -> tuple[list[PaperRecord], int]:
        """Fetch global top N, sorted upstream, without crawling the whole graph.

        cited_by means outgoing references; cites means incoming citations.
        A single page suffices because the public limit is at most 100.
        """
        field = {"references": "cited_by", "citations": "cites"}[relation]
        params = {**self._request_params(), "filter": f"{field}:{work_id}",
                  "sort": "cited_by_count:desc", "per_page": min(max(limit, 1), 100),
                  "page": 1}
        data = self.http.get(self.name, self.url, params=params, min_interval=0.1).json()
        # Missing totals/results are malformed responses, not evidence of no links.
        total = data["meta"]["count"]
        if not isinstance(total, int) or total < 0:
            raise ValueError("invalid OpenAlex result count")
        return [self._parse(item, i + 1) for i, item in enumerate(data["results"])], total

    def _parse(self, item: dict[str, Any], rank: int | None) -> PaperRecord:
        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        oa = item.get("open_access") or {}
        authors = [
            {"display_name": (a.get("author") or {}).get("display_name"), "orcid": (a.get("author") or {}).get("orcid")}
            for a in item.get("authorships") or []
        ]
        abstract = restore_abstract(item.get("abstract_inverted_index"))
        return PaperRecord(
            title=item.get("title") or item.get("display_name") or "[untitled]",
            authors=author_names(authors), year=item.get("publication_year"),
            published_date=item.get("publication_date"), journal=source.get("display_name"),
            document_type=item.get("type"), doi=normalize_doi(item.get("doi")),
            openalex_id=(item.get("id") or "").rsplit("/", 1)[-1] or None,
            abstract=abstract, abstract_source=self.name if abstract else None,
            url=location.get("landing_page_url") or item.get("id"), oa_url=oa.get("oa_url"),
            publication_status="published" if item.get("publication_date") else None,
            source=self.name, source_rank=rank, citation_count=item.get("cited_by_count"),
            field_sources=provenance(self.name, title=item.get("title"), authors=authors,
                                     year=item.get("publication_year"), journal=source.get("display_name"),
                                     doi=item.get("doi"), abstract=abstract, oa_url=oa.get("oa_url"),
                                     citation_count=item.get("cited_by_count")), raw=item,
        )
