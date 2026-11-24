import gzip
import json
import zlib

import httpx

from researchlib.http import HTTPClient


def test_retry_after_and_memory_cache(http_settings):
    calls, sleeps = [], []
    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)
    client = HTTPClient(http_settings, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    assert client.get("source", "https://example.test", params={"q": "x"}).json() == {"ok": True}
    assert client.get("source", "https://example.test", params={"q": "x"}).json() == {"ok": True}
    assert len(calls) == 2 and sleeps == [0.01]


def test_cache_is_process_memory_only(http_settings):
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"call": calls}, request=request)
    transport = httpx.MockTransport(handler)
    first = HTTPClient(http_settings, transport=transport)
    assert first.get("s", "https://example.test").json()["call"] == 1
    assert first.get("s", "https://example.test").json()["call"] == 1
    second = HTTPClient(http_settings, transport=transport)
    assert second.get("s", "https://example.test").json()["call"] == 2



def test_compressed_response_replay_does_not_double_decompress(http_settings):
    """Cached body is decoded; stale Content-Encoding must not be replayed."""
    for encoding, compressor in (("gzip", gzip.compress), ("deflate", zlib.compress)):
        calls = 0
        payload = {"message": {"DOI": "10.64898/2026.09.29.755517", "title": ["Test"]}}
        raw = compressor(json.dumps(payload).encode("utf-8"))

        def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "Content-Encoding": encoding,
                    "Content-Length": str(len(raw)),
                },
                content=raw,
                request=request,
            )

        client = HTTPClient(http_settings, transport=httpx.MockTransport(handler))
        try:
            url = "https://api.crossref.org/works/10.64898%2F2026.09.29.755517"
            assert client.get("crossref", url).json() == payload
            cached = client.get("crossref", url)
            assert cached.json() == payload
            assert cached.headers.get("Content-Encoding") is None
            assert int(cached.headers["Content-Length"]) == len(json.dumps(payload).encode())
            assert calls == 1
        finally:
            client.close()
