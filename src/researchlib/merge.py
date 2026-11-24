"""Conservative, transitive identifier-based deduplication."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .models import PaperRecord
from .providers.utils import normalize_doi

ABSTRACT_AUTHORITY = {
    "pubmed": 100, "europe_pmc": 95, "openalex": 65,
    "crossref": 50, "arxiv": 45, "biorxiv": 45, "medrxiv": 45,
}
IDENTIFIERS = ("doi", "pmid", "pmcid", "arxiv_id", "openalex_id")


def normalize_title(title: str) -> str:
    value = unicodedata.normalize("NFKD", title).casefold()
    return " ".join(re.findall(r"[\w]+", value))


@dataclass
class MergedRecord:
    paper: PaperRecord
    sources: list[str] = field(default_factory=list)
    source_ranks: dict[str, int] = field(default_factory=dict)
    query_ranks: dict[str, int] = field(default_factory=dict)


def _identifier_keys(record: PaperRecord) -> list[str]:
    keys: list[str] = []
    for field_name in IDENTIFIERS:
        value = normalize_doi(record.doi) if field_name == "doi" else getattr(record, field_name)
        if value:
            keys.append(f"{field_name}:{str(value).strip().casefold()}")
    if not keys:
        title = normalize_title(record.title)
        author = record.authors[0].name.casefold().split()[-1] if record.authors else ""
        if len(title) >= 15 and author and record.year:
            keys.append(f"fallback:{title}|{author}|{record.year}")
    return keys


def _abstract_score(record: PaperRecord) -> float:
    if not record.abstract:
        return -1
    source = record.abstract_source or record.source
    return ABSTRACT_AUTHORITY.get(source, 0) + min(len(record.abstract) / 1000, 5)


def _compatible(left: dict[str, set[str]], right: dict[str, set[str]]) -> bool:
    """Refuse a link when both groups have contradictory strong IDs."""
    for scheme in IDENTIFIERS:
        a, b = left.get(scheme, set()), right.get(scheme, set())
        if a and b and a.isdisjoint(b):
            return False
    return True


def merge_records(records: list[PaperRecord]) -> list[MergedRecord]:
    if not records:
        return []
    copies = [record.model_copy(deep=True) for record in records]
    for record in copies:
        record.doi = normalize_doi(record.doi)

    parent = list(range(len(copies)))
    keys_for: list[dict[str, set[str]]] = []
    for record in copies:
        identifiers: dict[str, set[str]] = {}
        for key in _identifier_keys(record):
            scheme, value = key.split(":", 1)
            identifiers.setdefault(scheme, set()).add(value)
        keys_for.append(identifiers)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> bool:
        a, b = find(i), find(j)
        if a == b:
            return True
        if not _compatible(keys_for[a], keys_for[b]):
            return False
        parent[b] = a
        for scheme, values in keys_for[b].items():
            keys_for[a].setdefault(scheme, set()).update(values)
        return True

    indexed: dict[str, list[int]] = {}
    for i, record in enumerate(copies):
        for key in _identifier_keys(record):
            for previous in indexed.get(key, []):
                union(i, previous)
            indexed.setdefault(key, []).append(i)

    grouped: dict[int, MergedRecord] = {}
    for i, record in enumerate(copies):
        root = find(i)
        if root not in grouped:
            grouped[root] = MergedRecord(paper=record)
        else:
            _merge_into(grouped[root].paper, record)
        target = grouped[root]
        if record.source not in target.sources:
            target.sources.append(record.source)
        rank = record.source_rank if record.source_rank is not None else 1
        target.source_ranks[record.source] = min(target.source_ranks.get(record.source, rank), rank)
        for query_key, query_rank in record.query_ranks.items():
            target.query_ranks[query_key] = min(target.query_ranks.get(query_key, query_rank), query_rank)
    return list(grouped.values())


def _merge_into(current: PaperRecord, incoming: PaperRecord) -> None:
    for name in (
        "year", "published_date", "journal", "document_type", "doi", "pmid", "pmcid",
        "arxiv_id", "openalex_id", "url", "oa_url", "publication_status",
        "preprint_version", "published_doi",
    ):
        if getattr(current, name) is None and getattr(incoming, name) is not None:
            setattr(current, name, getattr(incoming, name))
            current.field_sources[name] = incoming.source
    if len(incoming.authors) > len(current.authors):
        current.authors = incoming.authors
        current.field_sources["authors"] = incoming.source
    if _abstract_score(incoming) > _abstract_score(current):
        current.abstract = incoming.abstract
        current.abstract_source = incoming.abstract_source or incoming.source
        current.field_sources["abstract"] = incoming.abstract_source or incoming.source
