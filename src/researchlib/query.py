"""Small deterministic adapters for non-interchangeable provider query syntaxes.

This is a conservative compiler, not a complete Boolean expression parser.
Providers that accept Boolean syntax receive it directly; plain search endpoints
receive positive terms only. The original query is preserved for local ranking.
"""
from __future__ import annotations

import re


_NEGATED_GROUP = re.compile(r"\b(?:AND\s+)?NOT\s*\([^()]*\)", re.I)
_NEGATED_TOKEN = re.compile(r'\b(?:AND\s+)?NOT\s+(?:"[^"]+"|[\w.-]+)', re.I)


def positive_query(query: str) -> str:
    """Remove explicitly excluded clauses from relevance-bearing text."""
    cleaned = query
    while True:
        updated = _NEGATED_GROUP.sub(" ", cleaned)
        if updated == cleaned:
            break
        cleaned = updated
    return _NEGATED_TOKEN.sub(" ", cleaned)


_PARTS = re.compile(r'"[^"]+"|\(|\)|\b(?:AND|OR|NOT)\b|[^\s()]+', re.I)


def plain_query(query: str) -> str:
    """Remove Boolean syntax/negative clauses for plain-text search APIs."""
    clean = positive_query(query)
    clean = re.sub(r"\b(?:AND|OR|NOT)\b", " ", clean, flags=re.I)
    clean = re.sub(r'[()"]', ' ', clean)
    return " ".join(clean.split())


def arxiv_query(query: str) -> str:
    """Compile common AND/OR/NOT expression into arXiv all: terms."""
    if "all:" in query or "submittedDate:" in query:
        return query  # API-native queries used by the preprint tracker
    tokens = _PARTS.findall(query)
    output: list[str] = []
    previous_operand = False
    for token in tokens:
        upper = token.upper()
        if upper in {"AND", "OR"}:
            if output and output[-1] not in {"AND", "OR", "ANDNOT", "("}:
                output.append(upper)
            previous_operand = False
        elif upper == "NOT":
            if output and output[-1] == "AND":
                output.pop()
            output.append("ANDNOT")
            previous_operand = False
        elif token == "(":
            if previous_operand:
                output.append("AND")
            output.append("(")
            previous_operand = False
        elif token == ")":
            if output and output[-1] in {"AND", "OR", "ANDNOT"}:
                output.pop()
            output.append(")")
            previous_operand = True
        else:
            if previous_operand:
                output.append("AND")
            literal = token.strip('"')
            output.append(f'all:"{literal}"')
            previous_operand = True
    return " ".join(output)


def europe_pmc_query(query: str) -> str:
    # Europe PMC uses explicit AND NOT in its Boolean query grammar.
    return re.sub(r"(?<!AND)\s+NOT\s+", " AND NOT ", query, flags=re.I)


def plain_variants(query: str, max_variants: int = 4) -> list[str]:
    """Turn OR-heavy Boolean strings into bounded simple searches.

    Search engines such as OpenAlex/Crossref do not consistently interpret the
    Boolean grouping used for PubMed; joining all synonyms loses recall.
    A bounded round-robin set of AND combinations avoids a Cartesian explosion.
    """
    cleaned = positive_query(query)
    groups = list(re.finditer(r"\(([^()]*)\)", cleaned))
    alternatives: list[list[str]] = []
    fixed: list[str] = []
    for found in groups:
        inner = found.group(1)
        if re.search(r"\s+OR\s+", inner, flags=re.I):
            parts = [plain_query(part) for part in re.split(r"\s+OR\s+", inner, flags=re.I)]
            values = list(dict.fromkeys(part for part in parts if part))
            if values:
                alternatives.append(values)
        else:
            term = plain_query(inner)
            if term:
                fixed.append(term)
    outside = re.sub(r"\([^()]*\)", " ", cleaned)
    if not alternatives and re.search(r"\s+OR\s+", outside, flags=re.I):
        alternatives.append([plain_query(v) for v in re.split(r"\s+OR\s+", outside, flags=re.I) if plain_query(v)])
    else:
        remaining = plain_query(outside)
        if remaining:
            fixed.append(remaining)
    if not alternatives:
        return [plain_query(cleaned)] if plain_query(cleaned) else []
    limit = max(1, min(max_variants, 16))
    results: list[str] = []
    for i in range(limit):
        selected = fixed + [values[i % len(values)] for values in alternatives]
        value = " ".join(selected)
        if value and value not in results:
            results.append(value)
    return results
