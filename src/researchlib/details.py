"""On-demand detail retrieval, with no disk storage or model API dependency."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .http import HTTPClient
from .merge import merge_records
from .models import PaperRecord
from .providers.arxiv import ArxivProvider
from .providers.crossref import CrossrefProvider
from .providers.europe_pmc import EuropePMCProvider
from .providers.openalex import OpenAlexProvider
from .providers.pubmed import PubMedProvider
from .providers.utils import normalize_doi


class DetailsService:
    def __init__(self, http: HTTPClient):
        self.http = http

    @staticmethod
    def classify(identifier: str) -> tuple[str, str] | None:
        value = identifier.strip()
        doi = normalize_doi(value)
        if doi:
            return "doi", doi
        if re.fullmatch(r"(?:pmid:)?\d+", value, re.I):
            return "pmid", value.split(":")[-1]
        m = re.fullmatch(r"(?:https?://openalex\.org/|openalex:)?(W\d+)", value, re.I)
        if m:
            return "openalex", m.group(1).upper()
        m = re.fullmatch(r"(?:https?://(?:export\.)?arxiv\.org/abs/|arxiv:)([\w.\-/]+)", value, re.I)
        if m:
            return "arxiv", re.sub(r"v\d+$", "", m.group(1))
        return None

    def fetch(self, identifiers: list[str], *, max_items: int = 15) -> dict[str, Any]:
        if not 1 <= len(identifiers) <= max_items:
            return {"error": f"provide 1 to {max_items} identifiers"}
        unique = list(dict.fromkeys(value.strip() for value in identifiers))
        classified = {value: self.classify(value) for value in unique}
        invalid = [value for value, parsed in classified.items() if parsed is None]
        valid = {value: parsed for value, parsed in classified.items() if parsed is not None}
        if not valid:
            return {"results": [], "invalid": invalid, "not_found": [], "failures": {}, "complete": not invalid}

        records: list[PaperRecord] = []
        failures: dict[str, str] = {}
        # Each provider is allowed to fail independently. Europe PMC is valuable for
        # biomedical abstracts that are absent from DOI registration metadata.
        jobs: list[tuple[str, str, Any, dict[str, str]]] = []
        pmids = [parsed[1] for parsed in valid.values() if parsed[0] == "pmid"]
        if pmids:
            jobs.append(("pubmed", "batch", PubMedProvider(self.http).enrich, {"batch": ""}))
        for original, parsed in valid.items():
            scheme, ident = parsed
            data = {scheme: ident}
            if scheme == "doi":
                jobs.append(("europe_pmc", original, EuropePMCProvider(self.http, 1).enrich, data))
                jobs.append(("crossref", original, CrossrefProvider(self.http, 1).enrich, data))
            elif scheme == "openalex":
                jobs.append(("openalex", original, OpenAlexProvider(self.http, 1).enrich, data))
            elif scheme == "arxiv":
                jobs.append(("arxiv", original, ArxivProvider(self.http).enrich, data))
        with ThreadPoolExecutor(max_workers=min(6, len(jobs) or 1)) as executor:
            futures = {}
            for source, key, callback, data in jobs:
                payload = [{"pmid": pmid} for pmid in pmids] if source == "pubmed" else [data]
                futures[executor.submit(callback, payload)] = (source, key)
            for future in as_completed(futures):
                source, key = futures[future]
                try:
                    records.extend(future.result())
                except Exception as exc:
                    failures[f"{source}:{key}"] = f"{type(exc).__name__}: {str(exc)[:200]}"

        output = []
        missing = []
        for original, (scheme, value) in valid.items():
            matched = [record for record in records if getattr(record, "arxiv_id" if scheme == "arxiv" else "openalex_id" if scheme == "openalex" else scheme) == value]
            if not matched:
                missing.append(original)
                continue
            # One identifier should produce one merged paper. Never mix unrelated
            # results returned by a broadly matching upstream search.
            groups = merge_records(matched)
            best = max(groups, key=lambda group: (bool(group.paper.abstract), len(group.sources)))
            output.append({
                "requested_id": original,
                "sources": sorted(best.sources),
                "record": best.paper.model_dump(mode="json", exclude={"query_ranks"}),
            })
        return {
            "results": output,
            "invalid": invalid,
            "not_found": missing if not failures else [],
            "unresolved": missing if failures else [],
            "failures": failures,
            "complete": not invalid and not missing and not failures,
        }
