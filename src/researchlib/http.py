from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

from .config import HTTPSettings


class RequestBudgetExceeded(RuntimeError):
    pass


class SourceCoolingDown(RuntimeError):
    pass


@dataclass
class SourceState:
    last_request_at: float | None = None
    consecutive_429: int = 0
    cooldown_until: float = 0
    budget_window_start: float | None = None


@dataclass
class CacheEntry:
    status_code: int
    headers: dict[str, str]
    body: bytes
    expires_at: float


class HTTPClient:
    """Stateless HTTP client with process-memory cache and per-source limits."""

    def __init__(
        self,
        settings: HTTPSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        jitter: Callable[[float, float], float] = random.uniform,
    ):
        self.settings = settings
        self.sleep, self.monotonic, self.wall_clock, self.jitter = sleep, monotonic, wall_clock, jitter
        self.counts: dict[str, int] = defaultdict(int)
        self._states: dict[str, SourceState] = defaultdict(SourceState)
        self._source_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._cache: dict[str, CacheEntry] = {}
        self._cache_lock = threading.Lock()
        self.client = httpx.Client(timeout=settings.timeout_seconds, transport=transport, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def get(
        self, source: str, url: str, *, params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None, min_interval: float = 0, cache: bool = True,
    ) -> httpx.Response:
        params = {key: value for key, value in (params or {}).items() if value is not None}
        key = hashlib.sha256(json.dumps([source, url, params, headers], sort_keys=True, default=str).encode()).hexdigest()
        if cache:
            with self._cache_lock:
                entry = self._cache.get(key)
                if entry and entry.expires_at > self.wall_clock():
                    return httpx.Response(
                        entry.status_code, headers=entry.headers, content=entry.body,
                        request=httpx.Request("GET", url, params=params),
                    )
        with self._source_locks[source]:
            response = self._get_with_retry(source, url, params, headers, min_interval)
        if cache and self.settings.memory_cache_ttl_seconds > 0:
            with self._cache_lock:
                if len(self._cache) >= self.settings.max_cache_entries:
                    now = self.wall_clock()
                    self._cache = {k: v for k, v in self._cache.items() if v.expires_at > now}
                    if len(self._cache) >= self.settings.max_cache_entries:
                        self._cache.pop(next(iter(self._cache)))
                # HTTPX automatically decompresses response.content. Reusing the
                # upstream Content-Encoding header with those already-decoded
                # bytes makes HTTPX attempt to decompress a second time when a
                # cached Response is reconstructed (httpx.DecodingError).
                # Remove the representation/transfer headers describing the
                # original encoded wire payload, and let HTTPX calculate the
                # decoded body length when serving cache hits.
                cached_headers = {
                    name: value
                    for name, value in response.headers.items()
                    if name.lower() not in {
                        "content-encoding", "content-length", "transfer-encoding"
                    }
                }
                self._cache[key] = CacheEntry(
                    response.status_code, cached_headers, response.content,
                    self.wall_clock() + self.settings.memory_cache_ttl_seconds,
                )
        return response

    def _get_with_retry(
        self, source: str, url: str, params: dict[str, Any], headers: dict[str, str] | None,
        min_interval: float,
    ) -> httpx.Response:
        state = self._states[source]
        now = self.monotonic()
        if state.cooldown_until > now:
            raise SourceCoolingDown(f"{source} cooling down for {state.cooldown_until - now:.1f}s")
        if state.cooldown_until:
            state.cooldown_until = 0
            state.consecutive_429 = 0
        for attempt in range(self.settings.max_retries + 1):
            now = self.monotonic()
            if (state.budget_window_start is None or
                    now - state.budget_window_start >= self.settings.request_budget_window_seconds):
                state.budget_window_start = now
                self.counts[source] = 0
            if self.counts[source] >= self.settings.request_budget_per_source:
                raise RequestBudgetExceeded(f"request budget exceeded for {source}")
            if state.last_request_at is not None:
                wait_for_slot = min_interval - (self.monotonic() - state.last_request_at)
                if wait_for_slot > 0:
                    self.sleep(wait_for_slot)
            self.counts[source] += 1
            response = self.client.get(url, params=params, headers=headers)
            state.last_request_at = self.monotonic()
            if response.status_code not in {429, 500, 502, 503, 504}:
                response.raise_for_status()
                state.consecutive_429 = 0
                return response
            state.consecutive_429 = state.consecutive_429 + 1 if response.status_code == 429 else 0
            wait = self._retry_after(response.headers.get("Retry-After"))
            if wait is not None:
                wait = min(wait, self.settings.max_backoff_seconds)
            if wait is None:
                base = min(self.settings.max_backoff_seconds, self.settings.backoff_seconds * 2**attempt)
                wait = min(self.settings.max_backoff_seconds, base + self.jitter(0, min(1, base / 4)))
            if response.status_code == 429 and state.consecutive_429 >= self.settings.consecutive_429_threshold:
                cooldown = max(wait, self.settings.cooldown_seconds)
                state.cooldown_until = self.monotonic() + cooldown
                raise SourceCoolingDown(f"{source} entered {cooldown:.1f}s cooldown after repeated 429")
            if attempt >= self.settings.max_retries:
                response.raise_for_status()
            self.sleep(wait)
        raise RuntimeError("unreachable")

    def _retry_after(self, value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0, float(value))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return max(0, parsed.timestamp() - self.wall_clock())
            except (TypeError, ValueError, OverflowError):
                return None
