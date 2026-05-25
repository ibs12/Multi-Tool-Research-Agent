"""
rag/chroma_store.py
-------------------
ChromaDB vector store for SEC filing chunks.

Architecture:
  FilingChunk objects → embed via sentence-transformers → store in ChromaDB
  Query string        → embed                           → cosine similarity search

Why ChromaDB:
  - Zero config: runs in-process, persists to disk automatically
  - No server needed: perfect for local dev and interview demos
  - Same API as production vector DBs: easy to swap for pgvector later

Embedding model:
  Uses sentence-transformers/all-MiniLM-L6-v2 (local, free, fast).
  384-dimensional embeddings — small enough to run on CPU in <100ms.
  We intentionally avoid the Anthropic embeddings API here to keep
  the RAG layer self-contained and avoid extra API costs.

Interview talking point:
  "I used a local sentence-transformer for embeddings to keep the RAG
   layer cheap and fast — it runs on CPU with no API calls. In production
   at Citi, you'd swap this for a centralised embedding service and
   pgvector on PostgreSQL for ACID compliance and horizontal scaling."
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.sec_fetcher import FilingChunk

# Lazy imports — only load heavy dependencies when first used
_chroma_client = None
_embedding_fn  = None

CHROMA_PERSIST_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    ".chroma_db"
)
COLLECTION_NAME = "sec_filings"
TOP_K           = 5     # results to return per query
MIN_RELEVANCE   = 0.3   # cosine similarity threshold (0–1)


# -- Lazy initialisation ------------------------------------------------------

def _get_client():
    """Initialise ChromaDB client on first use."""
    global _chroma_client
    if _chroma_client is None:
        try:
            import chromadb
            _chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
        except ImportError:
            raise ImportError("Run: pip install chromadb")
    return _chroma_client


def _get_embedding_fn():
    """Initialise sentence-transformer embedding function on first use."""
    global _embedding_fn
    if _embedding_fn is None:
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            _embedding_fn = SentenceTransformerEmbeddingFunction(
                model_name="all-MiniLM-L6-v2"
            )
        except ImportError:
            raise ImportError("Run: pip install sentence-transformers")
    return _embedding_fn


def _get_collection():
    """Get or create the SEC filings collection."""
    client = _get_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=_get_embedding_fn(),
        metadata={"hnsw:space": "cosine"},
    )


# -- Public API ---------------------------------------------------------------

def ingest_chunks(chunks: list) -> int:
    """
    Embed and store filing chunks in ChromaDB.

    Uses content hashing as the document ID so re-ingesting the same
    filing is idempotent — no duplicate chunks accumulate over time.

    Returns the number of new chunks added.
    """
    if not chunks:
        return 0

    collection = _get_collection()

    ids        = []
    documents  = []
    metadatas  = []

    for chunk in chunks:
        # Deterministic ID from content hash — prevents duplicates
        chunk_id = hashlib.md5(
            f"{chunk.source_url}:{chunk.chunk_index}".encode()
        ).hexdigest()

        ids.append(chunk_id)
        documents.append(chunk.text)
        metadatas.append({
            "source_url":  chunk.source_url,
            "form_type":   chunk.form_type,
            "company":     chunk.company,
            "filed_at":    chunk.filed_at,
            "section":     chunk.section,
            "chunk_index": chunk.chunk_index,
        })

    # ChromaDB upsert is idempotent — safe to call multiple times
    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
    )
    return len(ids)


def query(
    query_text: str,
    company_filter: str | None = None,
    top_k: int = TOP_K,
) -> list[dict]:
    """
    Semantic search over stored SEC filing chunks.

    Args:
        query_text:     Natural language query e.g. "revenue growth rate"
        company_filter: Optional company name to restrict results
        top_k:          Number of results to return

    Returns:
        List of result dicts with text, metadata, and similarity score.
    """
    collection = _get_collection()

    # Check if collection has any documents
    if collection.count() == 0:
        return []

    try:
        # Try with company filter first for precision
        if company_filter:
            try:
                results = collection.query(
                    query_texts=[query_text],
                    n_results=min(top_k, collection.count()),
                    where={"company": {"$eq": company_filter}},
                    include=["documents", "metadatas", "distances"],
                )
                # Fall back to unfiltered if no results
                if not results.get("documents", [[]])[0]:
                    raise ValueError("No filtered results")
            except Exception:
                results = collection.query(
                    query_texts=[query_text],
                    n_results=min(top_k, collection.count()),
                    include=["documents", "metadatas", "distances"],
                )
        else:
            results = collection.query(
                query_texts=[query_text],
                n_results=min(top_k, collection.count()),
                include=["documents", "metadatas", "distances"],
            )
    except Exception:
        return []

    # Unpack results
    output = []
    docs       = results.get("documents", [[]])[0]
    metas      = results.get("metadatas", [[]])[0]
    distances  = results.get("distances",  [[]])[0]

    for doc, meta, dist in zip(docs, metas, distances):
        # ChromaDB cosine distance: 0 = identical, 2 = opposite
        # Convert to similarity: 1 - (dist / 2)
        similarity = round(1 - (dist / 2), 3)
        if similarity < MIN_RELEVANCE:
            continue
        output.append({
            "text":       doc,
            "similarity": similarity,
            "form_type":  meta.get("form_type", ""),
            "company":    meta.get("company", ""),
            "filed_at":   meta.get("filed_at", ""),
            "section":    meta.get("section", ""),
            "source_url": meta.get("source_url", ""),
        })

    # Sort by similarity descending
    output.sort(key=lambda x: x["similarity"], reverse=True)
    return output


def format_rag_results(results: list[dict], query_text: str) -> str:
    """Format RAG results as clean text for injection into agent state."""
    lines = [
        f"SEC FILING RAG RESULTS for: '{query_text}'",
        "=" * 60,
    ]

    if not results:
        lines.append("No relevant passages found in indexed SEC filings.")
        return "\n".join(lines)

    for i, r in enumerate(results, 1):
        lines += [
            f"[{i}] {r['form_type']} — {r['company']} (filed {r['filed_at']})",
            f"    Section:    {r['section']}",
            f"    Similarity: {r['similarity']:.2f}",
            f"    Source:     {r['source_url']}",
            f"    Passage:",
            f"    {r['text'][:600]}",
            "",
        ]

    return "\n".join(lines)


def collection_stats() -> dict:
    """Return stats about the current vector store state, including per-company counts."""
    try:
        collection = _get_collection()
        count = collection.count()
        companies: dict[str, int] = {}
        if count > 0:
            all_meta = collection.get(include=["metadatas"])
            for meta in all_meta.get("metadatas") or []:
                company = meta.get("company", "unknown")
                companies[company] = companies.get(company, 0) + 1
        return {"total_chunks": count, "collection": COLLECTION_NAME, "companies": companies}
    except Exception as e:
        return {"error": str(e), "total_chunks": 0, "companies": {}}