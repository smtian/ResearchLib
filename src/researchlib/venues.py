from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class VenueFetch(BaseModel):
    rows: int = 50
    overlap_days: int = 2
    max_pages: int = 2


class Venue(BaseModel):
    slug: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    issn_print: str | None = None
    issn_online: str | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    fetch: VenueFetch = Field(default_factory=VenueFetch)
    verified_by: str

    @field_validator("issn_print", "issn_online")
    @classmethod
    def valid_issn_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.upper()
        if not re.fullmatch(r"\d{4}-\d{3}[\dX]", normalized):
            raise ValueError(f"invalid ISSN: {value}")
        compact = normalized.replace("-", "")
        total = sum(int(digit) * weight for digit, weight in zip(compact[:7], range(8, 1, -1)))
        check = (11 - total % 11) % 11
        expected = "X" if check == 10 else str(check)
        if compact[-1] != expected:
            raise ValueError(f"invalid ISSN checksum: {value}")
        return normalized

    @property
    def issns(self) -> list[str]:
        return list(dict.fromkeys(value for value in (self.issn_print, self.issn_online) if value))

    @property
    def accepted_names(self) -> set[str]:
        return {self.name.casefold(), *(alias.casefold() for alias in self.aliases)}


def load_venues(path: str | Path) -> dict[str, Venue]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return {
        slug: Venue(slug=slug, **values)
        for slug, values in (data.get("venues") or {}).items()
    }


def find_venue(venues: dict[str, Venue], value: str) -> Venue:
    key = value.casefold()
    if key in venues:
        return venues[key]
    matches = [venue for venue in venues.values() if key in venue.accepted_names]
    if len(matches) != 1:
        raise KeyError(f"unknown or ambiguous venue: {value}")
    return matches[0]
