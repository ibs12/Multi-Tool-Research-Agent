"""
mcp_server/server.py
────────────────────
Standalone **document-RAG** MCP server (ADR-0008). A reusable capability —
ingest a document, semantically search ingested documents — exposed over the
Model Context Protocol so any agent (this project's research/risk/compliance
agents, or a second consumer like Lumen) can call it.

Deliberately generic, not SEC-locked (ADR-0008):
    entity        ↔ the org a document is about   (backing column: company)
    document_type ↔ the kind of document          (backing column: form_type)
    published_at  ↔ ISO date                       (backing column: filed_at)
The backing store keeps its filing-flavored column names; this boundary maps
the generic surface onto them so no store migration is needed.

The store is server-encapsulated — callers touch tools, never the vector DB.
It reuses the dual pgvector/Chroma backend (ADR-0005): with no PGVECTOR_URL the
server falls back to embedded Chroma, so a stdio subprocess launch has **zero
external dependencies** (ADR-0010) — the Map 2 CI eval-gate's real MCP path.

Run directly:
    python -m mcp_server.server                       # stdio (default)
    MCP_TRANSPORT=streamable-http python -m mcp_server.server   # HTTP (deploy)
"""

from __future__ import annotations

import asyncio
import os

from fastmcp import FastMCP

from rag.sec_fetcher import fetch_and_chunk
from rag import rag_backend

mcp = FastMCP("document-rag")


@mcp.tool
async def ingest_document(
    url: str,
    entity: str,
    document_type: str,
    published_at: str = "",
) -> dict:
    """Fetch, chunk, embed, and store one document so it becomes searchable.

    `entity` is the organisation the document is about (e.g. 'Apple Inc.'),
    `document_type` its kind (e.g. '10-K', 'news'), `published_at` an ISO date.
    Returns the number of chunks stored (0 with an `error` if nothing could be
    fetched/parsed — never raises, so a bad URL degrades gracefully).
    """
    chunks = await asyncio.to_thread(
        fetch_and_chunk,
        url=url,
        form_type=document_type,
        company=entity,
        filed_at=published_at,
    )
    if not chunks:
        return {
            "chunks_ingested": 0,
            "url": url,
            "entity": entity,
            "document_type": document_type,
            "error": "no content fetched or parsed",
        }
    n = await rag_backend.ingest_chunks(chunks)
    return {
        "chunks_ingested": n,
        "url": url,
        "entity": entity,
        "document_type": document_type,
    }


@mcp.tool
async def search_documents(
    query: str,
    entity: str | None = None,
    document_type: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    """Semantic search over ingested documents.

    Returns **structured per-chunk hits** — text plus provenance
    (entity, document_type, published_at, section, source_url, similarity) —
    NOT a display string. The caller composes citations from the fields, which
    is what lets citations survive across agents (ADR-0008). Optionally filter
    by `entity` and/or `document_type`.
    """
    raw = await rag_backend.query(query_text=query, company_filter=entity, top_k=top_k)
    hits: list[dict] = []
    for r in raw:
        if document_type and r.get("form_type") != document_type:
            continue
        hits.append({
            "text":          r.get("text", ""),
            "similarity":    r.get("similarity"),
            "entity":        r.get("company", ""),
            "document_type": r.get("form_type", ""),
            "published_at":  r.get("filed_at", ""),
            "section":       r.get("section", ""),
            "source_url":    r.get("source_url", ""),
        })
    return hits


@mcp.tool
async def collection_stats() -> dict:
    """Introspection: chunk/document counts currently held by the store."""
    stats = await rag_backend.collection_stats()
    return stats if isinstance(stats, dict) else {"stats": stats}


# ── Transport selection (ADR-0010) ────────────────────────────────────────────

def _transport_from_env() -> tuple[str, dict]:
    """stdio locally/CI (server as a subprocess, embedded store → zero external
    deps); streamable-http in deploy (a networked Railway service)."""
    t = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if t in ("http", "streamable-http", "streamable_http"):
        return "streamable-http", {
            "host": os.getenv("MCP_HOST", "0.0.0.0"),
            "port": int(os.getenv("MCP_PORT", "8100")),
        }
    return "stdio", {}


if __name__ == "__main__":
    transport, kwargs = _transport_from_env()
    # In stdio mode stdout is the JSON-RPC channel; keep the banner off so it
    # can't clutter CI logs (it prints to stderr, but silence is cleaner).
    mcp.run(transport=transport, show_banner=(transport != "stdio"), **kwargs)
