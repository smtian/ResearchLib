from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class HTTPSettings(BaseModel):
    timeout_seconds: float = 20
    max_retries: int = 3
    backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30
    cooldown_seconds: float = 60
    consecutive_429_threshold: int = 3
    memory_cache_ttl_seconds: int = 300
    request_budget_per_source: int = 100
    request_budget_window_seconds: float = 60
    max_cache_entries: int = 2048


class ProviderSettings(BaseModel):
    enabled: bool = True
    per_page: int = 50
    semantic_search: bool = False


class SearchSettings(BaseModel):
    quick_candidate_limit: int = 300
    deep_candidate_limit: int = 1500
    max_pages_per_query: int = 5
    default_result_limit: int = 10


class TrackingSettings(BaseModel):
    topics_file: Path = Path("config/topics.yml")
    venues_file: Path = Path("config/venues.yml")
    arxiv_page_size: int = 50
    arxiv_min_interval: float = 3.0
    preprint_page_size: int = 100
    max_pages_per_source: int = 3


class Settings(BaseModel):
    http: HTTPSettings = Field(default_factory=HTTPSettings)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    search: SearchSettings = Field(default_factory=SearchSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value.strip().strip("\"'"))


def load_settings(path: Path | None = None) -> Settings:
    load_dotenv()
    config_path = path or Path(os.getenv("RESEARCHLIB_CONFIG", "config/default.yaml"))
    data: dict[str, Any] = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    return Settings.model_validate(data or {})
