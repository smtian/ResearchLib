"""Local, explainable candidate ranking; citations are never used as relevance."""
from __future__ import annotations

import math
import re
from collections import Counter

from .merge import MergedRecord
from .query import positive_query
from .models import SearchHit

_TOKEN = re.compile(r"[\w-]{2,}", re.UNICODE)
_STOP = {"and", "or", "not", "all", "to", "the", "of", "in", "for", "with"}


def query_terms(queries: list[str]) -> list[str]:
    return list(dict.fromkeys(
        term for query in queries
        for term in _TOKEN.findall(positive_query(query).casefold())
        if term not in _STOP
    ))


def rank_records(records: list[MergedRecord], queries: list[str]) -> list[SearchHit]:
    if not records:
        return []
    terms = query_terms(queries)
    documents = [Counter(_TOKEN.findall(
        f"{item.paper.title} {item.paper.abstract or ''}".casefold())) for item in records]
    titles = [set(_TOKEN.findall(item.paper.title.casefold())) for item in records]
    lengths = [sum(counts.values()) for counts in documents]
    avg_length = sum(lengths) / max(len(lengths), 1)
    term_set = set(terms)
    df = Counter(term for document in documents for term in document if term in term_set)
    hits = []
    for item, counts, title_tokens, length in zip(records, documents, titles, lengths):
        keyword = sum(3 if term in title_tokens else 1 if term in counts else 0 for term in terms)
        bm25 = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            inverse = math.log1p((len(records) - df[term] + 0.5) / (df[term] + 0.5))
            denominator = tf + 1.2 * (0.25 + 0.75 * length / max(avg_length, 1))
            bm25 += inverse * tf * 2.2 / denominator
        ranks = item.query_ranks or item.source_ranks
        rrf = sum(1 / (60 + rank) for rank in ranks.values())
        # Bounded lexical contributions keep a flood of synonyms from dominating.
        lexical = keyword / max(3 * len(terms), 1)
        score = 0.5 * lexical + 0.35 * bm25 / (bm25 + 5) + 0.15 * min(rrf * 60, 1)
        hits.append(SearchHit(
            paper=item.paper, sources=sorted(item.sources), source_ranks=item.source_ranks,
            score=round(score, 6), keyword_score=float(keyword), bm25_score=round(bm25, 6),
        ))
    return sorted(hits, key=lambda hit: (-hit.score, hit.paper.title.casefold()))
