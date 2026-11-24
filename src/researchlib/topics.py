from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Topic(BaseModel):
    slug: str
    name: str
    strategy: str = "OR"
    synonyms: list[str] = Field(default_factory=list)
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    related_terms: list[str] = Field(default_factory=list)
    chinese_terms: dict[str, str] = Field(default_factory=dict)


class UnreliableTranslationError(ValueError):
    pass


def load_topics(path: str | Path) -> dict[str, Topic]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return {
        slug: Topic(slug=slug, **values)
        for slug, values in (data.get("topics") or {}).items()
    }


def _quote(term: str) -> str:
    clean = " ".join(term.split())
    return f'"{clean}"' if " " in clean else clean


def _group(terms: list[str]) -> str:
    unique = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    return "(" + " OR ".join(_quote(term) for term in unique) + ")"


def _with_excludes(query: str, excludes: list[str]) -> str:
    return f"{query} NOT {_group(excludes)}" if excludes else query


def expand_topic(topic: Topic) -> list[str]:
    """Build deterministic English queries without imposing one strategy globally."""
    strategy = topic.strategy.upper()
    queries: list[str] = []
    if strategy == "AND" and topic.include:
        core = _group(topic.synonyms)
        queries.append(_with_excludes(f"{core} AND {_group(topic.include)}", topic.exclude))
        if topic.related_terms:
            queries.append(_with_excludes(f"{core} AND {_group(topic.related_terms)}", topic.exclude))
    elif strategy == "OR":
        queries.append(_with_excludes(_group(topic.synonyms + topic.include), topic.exclude))
        if topic.related_terms and topic.synonyms:
            queries.append(_with_excludes(f"{_group(topic.synonyms)} AND {_group(topic.related_terms)}", topic.exclude))
    else:
        raise ValueError(f"unsupported strategy for {topic.slug}: {topic.strategy}")
    normalized: dict[str, str] = {}
    for query in queries:
        key = re.sub(r"\s+", " ", query).strip().casefold()
        normalized.setdefault(key, query)
    return list(normalized.values())


def translate_defined_terms(query: str, topics: dict[str, Topic]) -> str:
    """Translate only configured Chinese terms; reject any untranslated CJK text."""
    if not re.search(r"[\u3400-\u9fff]", query):
        return query
    mappings = {
        chinese: english
        for topic in topics.values()
        for chinese, english in topic.chinese_terms.items()
    }
    translated = query
    for chinese in sorted(mappings, key=len, reverse=True):
        translated = translated.replace(chinese, mappings[chinese])
    if re.search(r"[\u3400-\u9fff]", translated):
        raise UnreliableTranslationError("query contains Chinese terms without a configured English mapping")
    return " ".join(translated.split())


def expand_user_query(query: str, topics: dict[str, Topic]) -> list[str]:
    english = translate_defined_terms(query, topics)
    values = [english]
    folded = english.casefold()
    for topic in topics.values():
        known = topic.synonyms + topic.include + list(topic.chinese_terms.values())
        if any(term.casefold() in folded for term in known):
            values.extend(expand_topic(topic))
    unique: dict[str, str] = {}
    for value in values:
        unique.setdefault(re.sub(r"\s+", " ", value).strip().casefold(), value)
    return list(unique.values())
