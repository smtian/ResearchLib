from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Iterable

from ..models import DiscoveryPage, PaperRecord, SearchFilters


class Provider(ABC):
    name: str

    @abstractmethod
    def discover(self, query: str, filters: SearchFilters, cursor: str | None = None) -> DiscoveryPage:
        raise NotImplementedError

    @abstractmethod
    def enrich(self, identifiers: list[dict[str, str]]) -> list[PaperRecord]:
        raise NotImplementedError

    def track(self, start_date: date, end_date: date) -> Iterable[PaperRecord]:
        raise NotImplementedError("tracking is reserved for a future phase")


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        if provider.name in self._providers:
            raise ValueError(f"provider already registered: {provider.name}")
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider:
        return self._providers[name]

    def enabled(self) -> list[Provider]:
        return list(self._providers.values())

    def __iter__(self):
        return iter(self._providers.values())

