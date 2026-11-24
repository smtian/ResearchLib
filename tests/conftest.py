import pytest

from researchlib.config import HTTPSettings


@pytest.fixture
def http_settings():
    return HTTPSettings(
        timeout_seconds=2, max_retries=2, backoff_seconds=0,
        max_backoff_seconds=1, cooldown_seconds=10,
        consecutive_429_threshold=3, memory_cache_ttl_seconds=60,
        request_budget_per_source=20,
    )

