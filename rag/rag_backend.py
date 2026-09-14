"""
rag/rag_backend.py
------------------
Selects the vector-store backend at import time based on environment.

  PGVECTOR_URL set  →  PostgreSQL + pgvector  (production / Docker)
  PGVECTOR_URL unset →  ChromaDB              (local dev, no Docker)

Both backends expose an identical async public API so nothing else in the
codebase needs to branch on the backend choice.  ChromaDB functions are
sync at their core, so they are wrapped with asyncio.to_thread() here to
present a uniform async interface to callers.
"""

from __future__ import annotations

import os

if os.getenv("PGVECTOR_URL"):
    from rag.pgvector_store import (   # noqa: F401  — all async
        ingest_chunks,
        query,
        format_rag_results,
        collection_stats,
        existing_source_urls,
    )
    BACKEND = "pgvector"
else:
    import asyncio as _asyncio
    from rag.chroma_store import (
        ingest_chunks        as _ingest_sync,
        query                as _query_sync,
        format_rag_results,                    # pure function — stays sync
        collection_stats     as _stats_sync,
        existing_source_urls as _urls_sync,
    )

    async def ingest_chunks(chunks):       # noqa: F811
        return await _asyncio.to_thread(_ingest_sync, chunks)

    async def query(query_text, company_filter=None, top_k=5):  # noqa: F811
        return await _asyncio.to_thread(_query_sync, query_text, company_filter, top_k)

    async def collection_stats():          # noqa: F811
        return await _asyncio.to_thread(_stats_sync)

    async def existing_source_urls():      # noqa: F811
        return await _asyncio.to_thread(_urls_sync)

    BACKEND = "chromadb"
