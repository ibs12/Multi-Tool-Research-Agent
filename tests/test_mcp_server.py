"""
tests/test_mcp_server.py
------------------------
The standalone document-RAG MCP server (ADR-0008): the generic (not SEC-locked)
tool contract, structured search results, and graceful degradation. Exercised
through FastMCP's in-memory Client — the *real* MCP tool-registration and
call path (schemas, JSON-RPC serialisation), no subprocess or network.

Run with:
    pytest tests/test_mcp_server.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("fastmcp")
from fastmcp import Client  # noqa: E402
from mcp_server.server import mcp  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_tool_contract_is_generic_not_sec_locked():
    async def go():
        async with Client(mcp) as c:
            tools = {t.name: t for t in await c.list_tools()}
            assert set(tools) == {"ingest_document", "search_documents", "collection_stats"}
            ing = tools["ingest_document"].inputSchema["properties"]
            # generic names (ADR-0008) — not company/form_type/filed_at
            assert set(ing) >= {"url", "entity", "document_type", "published_at"}
            sd = tools["search_documents"].inputSchema["properties"]
            assert set(sd) >= {"query", "entity", "document_type", "top_k"}
    _run(go())


def test_collection_stats_returns_dict():
    async def go():
        async with Client(mcp) as c:
            res = await c.call_tool("collection_stats", {})
            data = res.data if hasattr(res, "data") else res.content
            assert isinstance(data, dict)
    _run(go())


def test_search_returns_structured_generic_hits():
    async def go():
        async with Client(mcp) as c:
            res = await c.call_tool("search_documents", {"query": "revenue", "top_k": 2})
            data = res.data if hasattr(res, "data") else res.content
            if isinstance(data, str):
                data = json.loads(data)
            assert isinstance(data, list)
            # If the local store has data, hits carry the generic provenance keys
            for h in data:
                assert set(h) >= {"text", "similarity", "entity",
                                  "document_type", "published_at", "section", "source_url"}
    _run(go())


def test_ingest_bad_url_degrades_without_raising():
    # A connection-refused URL fails fetch fast; the tool must return a 0-chunk
    # result with an error, never raise (the never-raise discipline, ADR-0003).
    async def go():
        async with Client(mcp) as c:
            res = await c.call_tool("ingest_document", {
                "url": "http://127.0.0.1:1/nonexistent",
                "entity": "Test Co",
                "document_type": "10-K",
                "published_at": "2024-01-01",
            })
            data = res.data if hasattr(res, "data") else res.content
            if isinstance(data, str):
                data = json.loads(data)
            assert data["chunks_ingested"] == 0
            assert "error" in data
    _run(go())
