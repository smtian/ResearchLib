"""Live citation neighborhoods; no persistence and no relevance scoring."""
from __future__ import annotations

import re

import httpx

from .http import HTTPClient
from .models import RelatedPaperList, RelatedPapersResult
from .providers.openalex import OpenAlexProvider
from .providers.utils import normalize_doi


def _failure(exc: Exception) -> str:
    # HTTP exceptions can include request URLs containing API keys or contact data.
    if isinstance(exc, httpx.HTTPStatusError):
        return f"OpenAlex HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: OpenAlex request or response processing failed"


class RelatedPapersService:
    def __init__(self, http: HTTPClient):
        self.provider = OpenAlexProvider(http)

    def fetch(self, doi: str, limit: int = 10) -> RelatedPapersResult:
        normalized = normalize_doi(doi)
        limit = min(max(limit, 1), 100)
        result = RelatedPapersResult(doi=normalized or doi, status="invalid", limit=limit)
        if not normalized:
            result.error = "invalid DOI format"
            return result
        try:
            paper = self.provider.resolve_doi(normalized)
            if paper is None:
                result.status = "not_found"
                return result
            if paper.doi != normalized or not re.fullmatch(r"W\d+", paper.openalex_id or ""):
                raise ValueError("OpenAlex returned a mismatched or invalid identifier")
            result.paper = paper
        except Exception as exc:
            result.status = "error"
            result.failures["openalex:doi"] = _failure(exc)
            return result

        for relation in ("references", "citations"):
            branch = RelatedPaperList()
            setattr(result, relation, branch)
            try:
                records, total = self.provider.related_works(paper.openalex_id, relation, limit)
                branch.total = total
                branch.raw_candidates = len(records)
                # Deduplicate exact OpenAlex identities; never merge by title.
                unique = {}
                for record in records:
                    if not re.fullmatch(r"W\d+", record.openalex_id or ""):
                        raise ValueError("OpenAlex returned an invalid work identifier")
                    previous = unique.get(record.openalex_id)
                    if previous is None or (
                        record.citation_count is not None and (
                            previous.citation_count is None or record.citation_count > previous.citation_count
                        )
                    ):
                        unique[record.openalex_id] = record
                ordered = sorted(unique.values(), key=lambda p: (
                    -(p.citation_count if p.citation_count is not None else -1), p.openalex_id,
                ))
                branch.unique_candidates = len(ordered)
                branch.results = ordered[:limit]
                branch.display_truncated = total > limit or len(ordered) > limit
                branch.candidates_truncated = len(branch.results) < min(total, limit)
                if branch.candidates_truncated:
                    branch.failures["openalex"] = "OpenAlex returned fewer unique works than the requested top N"
                branch.complete = not branch.candidates_truncated
            except Exception as exc:
                branch.candidates_truncated = True
                branch.failures["openalex"] = _failure(exc)
            branch.truncated = branch.candidates_truncated or branch.display_truncated
            result.failures.update({f"openalex:{relation}": message for message in branch.failures.values()})
        result.complete = result.references.complete and result.citations.complete
        result.status = "ok" if result.complete else "partial"
        return result
