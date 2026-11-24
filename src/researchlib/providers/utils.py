from __future__ import annotations

import html
import re
from typing import Any

from ..models import Author

DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = DOI_PREFIX_RE.sub("", value.strip()).strip().lower()
    doi = doi.rstrip(" .,;:)\"'")
    return doi if re.fullmatch(r"10\.\d{1,9}/\S+", doi, flags=re.I) else None


def clean_text(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", text).strip() or None


def year_from(value: Any) -> int | None:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return int(match.group()) if match else None


def author_names(values: list[Any] | None, *, key: str = "display_name") -> list[Author]:
    result = []
    for item in values or []:
        if isinstance(item, str):
            name, orcid = item, None
        else:
            name, orcid = item.get(key) or item.get("name"), item.get("orcid")
        if name:
            result.append(Author(name=name, orcid=orcid))
    return result


def provenance(source: str, **fields: Any) -> dict[str, str]:
    return {name: source for name, value in fields.items() if value not in (None, "", [])}

