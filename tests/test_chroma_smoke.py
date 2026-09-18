"""
tests/test_chroma_smoke.py
--------------------------
Smoke test for the ChromaDB backend so it doesn't rot silently (ADR-0005 —
only the pgvector path runs in the demo/deploy). The light checks catch the
common breakages — import errors, signature drift, rag_backend wiring — without
downloading the embedding model. The full ingest→query round-trip embeds text
(downloads all-MiniLM-L6-v2), so it's gated behind CHROMA_ROUNDTRIP=1 to keep the
fast suite fast and offline.

Run everything with:
    CHROMA_ROUNDTRIP=1 pytest tests/test_chroma_smoke.py -v
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import rag.chroma_store as cs
import rag.rag_backend as backend


_PUBLIC_API = ("ingest_chunks", "query", "collection_stats",
               "existing_source_urls", "format_rag_results")


def test_chroma_store_public_api_present():
    for fn in _PUBLIC_API:
        assert callable(getattr(cs, fn, None)), f"chroma_store.{fn} missing/not callable"


def test_backend_exposes_uniform_api():
    # rag_backend must expose the same names regardless of which store it picked
    # (ADR-0005) — this is what lets the pipeline layer stay backend-agnostic.
    for fn in _PUBLIC_API:
        assert hasattr(backend, fn), f"rag_backend.{fn} missing"


def test_chroma_format_empty_is_graceful():
    assert "No relevant passages" in cs.format_rag_results([], "revenue")


@pytest.mark.skipif(
    not os.getenv("CHROMA_ROUNDTRIP"),
    reason="set CHROMA_ROUNDTRIP=1 to run the embedding round-trip (downloads the model)",
)
def test_chroma_ingest_query_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    pytest.importorskip("sentence_transformers")

    # Isolate to a temp store and reset the module singletons.
    monkeypatch.setattr(cs, "CHROMA_PERSIST_DIR", str(tmp_path))
    monkeypatch.setattr(cs, "_chroma_client", None)
    monkeypatch.setattr(cs, "_embedding_fn", None)

    chunk = SimpleNamespace(
        text="Total net revenue was $391 billion for fiscal 2024.",
        source_url="http://example/aapl-10k",
        chunk_index=0,
        company="Apple Inc.",
        form_type="10-K",
        filed_at="2024-11-01",
        section="Financial Statements",
    )

    assert cs.ingest_chunks([chunk]) == 1
    assert "http://example/aapl-10k" in cs.existing_source_urls()

    results = cs.query("annual revenue", company_filter="Apple Inc.")
    assert results and "391 billion" in results[0]["text"]
