import asyncio
import os
from pathlib import Path
import sys

import pytest

pytest.importorskip("mcp", reason="Official MCP Python SDK is required for the real-client test")
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from researchlib.mcp_server import mcp


def test_mcp_tools_list_and_structured_call():
    async def run():
        async with Client(mcp) as client:
            listing = await client.list_tools()
            names = {tool.name for tool in listing.tools}
            assert names == {
                "search_literature", "lookup_doi", "track_venues", "track_preprints",
                "list_topics", "expand_query", "list_venues", "get_paper_details",
                "get_related_papers",
            }
            result = await client.call_tool("expand_query", {"topic": "small-orfs"})
            assert result.structured_content["topic"] == "small-orfs"
            assert result.structured_content["queries"]
            invalid = await client.call_tool("lookup_doi", {"doi": "not a doi"})
            assert invalid.structured_content["status"] == "invalid"
            no_topic = await client.call_tool("search_literature", {"query": "protein", "topic": "invalid-topic"})
            assert "unknown topic" in no_topic.structured_content["error"]
            related = await client.call_tool("get_related_papers", {"doi": "not a doi"})
            assert related.structured_content["status"] == "invalid"
            assert related.structured_content["complete"] is False
    asyncio.run(run())


def test_related_papers_real_sdk_mocked_openalex(monkeypatch, http_settings):
    import httpx
    import researchlib.mcp_server as server
    from researchlib.http import HTTPClient

    def handler(req):
        if req.url.path != "/works":
            return httpx.Response(200, json={"id": "https://openalex.org/W1",
                                           "doi": "10.1000/fixture", "title": "SDK fixture"})
        return httpx.Response(200, json={"meta": {"count": 1}, "results": [{
            "id": "https://openalex.org/W2", "title": "Linked fixture", "cited_by_count": 17,
        }]})

    http = HTTPClient(http_settings, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    monkeypatch.setattr(server, "_shared_http", lambda: http)

    async def run():
        async with Client(mcp) as client:
            response = await client.call_tool("get_related_papers", {"doi": "10.1000/fixture", "limit": 1})
            data = response.structured_content
            assert data["status"] == "ok" and data["complete"] is True
            assert data["references"]["results"][0]["citation_count"] == 17
            assert data["citations"]["results"][0]["sources"] == ["openalex"]
    try:
        asyncio.run(run())
    finally:
        http.close()


def test_console_entrypoint_stdio_all_nine_tools_without_external_apis():
    entrypoint = Path(sys.executable).parent / "researchlib-mcp"
    if not entrypoint.exists():
        pytest.skip("Installed researchlib-mcp entrypoint unavailable; stdio launch unverified")

    async def run():
        params = StdioServerParameters(
            command=str(entrypoint), cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        async with Client(params) as client:
            calls = {
                "search_literature": {"query": "protein", "topic": "invalid-topic"},
                "get_paper_details": {"identifiers": ["invalid identifier"]},
                "get_related_papers": {"doi": "invalid DOI"},
                "lookup_doi": {"doi": "invalid DOI"},
                "track_venues": {"venue": "invalid-venue"},
                "track_preprints": {"topic": "invalid-topic"},
                "list_topics": {}, "expand_query": {"topic": "small-orfs"}, "list_venues": {},
            }
            listing = await client.list_tools()
            assert {tool.name for tool in listing.tools} == set(calls)
            for name, arguments in calls.items():
                response = await client.call_tool(name, arguments)
                assert not response.is_error, name
                assert response.structured_content, name
                if name == "get_related_papers":
                    assert response.structured_content["status"] == "invalid"
    asyncio.run(run())
