"""
rag/rag_backend.py
------------------
Selects the vector-store backend at import time based on environment.

  PGVECTOR_URL set  →  PostgreSQL + pgvector  (production / Docker)
  PGVECTOR_URL unset →  ChromaDB              (local dev, no Docker)

Both backends expose an identical public API so nothing else in the
codebase needs to branch on the backend choice.
"""

from __future__ import annotations

import os

if os.getenv("PGVECTOR_URL"):
    from rag.pgvector_store import (   # noqa: F401
        ingest_chunks,
        query,
        format_rag_results,
        collection_stats,
    )
    BACKEND = "pgvector"
else:
    from rag.chroma_store import (     # noqa: F401
        ingest_chunks,
        query,
        format_rag_results,
        collection_stats,
    )
    BACKEND = "chromadb"
