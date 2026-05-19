"""
rag/pgvector_store.py
---------------------
PostgreSQL + pgvector drop-in replacement for chroma_store.py.

Exposes the identical public API:
    ingest_chunks(chunks)           -> int
    query(text, company, top_k)     -> list[dict]
    format_rag_results(results, q)  -> str
    collection_stats()              -> dict

Uses psycopg2 directly (not SQLAlchemy text()) to avoid the colon
conflict between SQLAlchemy's :param syntax and SEC filing content
that contains colons (e.g. us-gaap:RetainedEarningsMember).

Interview talking point:
    "I started with ChromaDB for local development — zero config, runs in
     process. Once that worked I migrated to pgvector. The abstraction layer
     meant only one file changed. In production at Citi you'd point this at
     a managed Aurora PostgreSQL instance with pgvector enabled — same code,
     enterprise scale."
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.sec_fetcher import FilingChunk

# ── Config ────────────────────────────────────────────────────────────────────
PGVECTOR_URL   = os.getenv("PGVECTOR_URL", "postgresql://postgres:ragpassword@localhost:5433/financial_agent")
TABLE_NAME     = "sec_filing_chunks"
EMBEDDING_DIM  = 384
TOP_K          = 5
MIN_SIMILARITY = 0.3

# ── Lazy globals ──────────────────────────────────────────────────────────────
_conn            = None
_embedding_model = None


# ── Connection ────────────────────────────────────────────────────────────────

def _get_conn():
    """Get a psycopg2 connection, creating it if needed."""
    global _conn
    try:
        if _conn is None or _conn.closed:
            import psycopg2
            _conn = psycopg2.connect(PGVECTOR_URL)
            _conn.autocommit = False
    except ImportError:
        raise ImportError("Run: pip install psycopg2-binary")
    return _conn


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            raise ImportError("Run: pip install sentence-transformers")
    return _embedding_model


def _embed(texts: list[str]) -> list[list[float]]:
    model = _get_embedding_model()
    return model.encode(texts, batch_size=32, show_progress_bar=False).tolist()


def _fmt_embedding(vec: list[float]) -> str:
    """Format as pgvector literal: '[0.123,0.456,...]'"""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


# ── Schema ────────────────────────────────────────────────────────────────────

def _ensure_schema():
    """Create pgvector extension and table if they don't exist. Idempotent."""
    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id           TEXT PRIMARY KEY,
                company      TEXT    NOT NULL,
                form_type    TEXT    NOT NULL,
                filed_at     TEXT    NOT NULL,
                section      TEXT    NOT NULL DEFAULT '',
                chunk_index  INTEGER NOT NULL DEFAULT 0,
                source_url   TEXT    NOT NULL DEFAULT '',
                content      TEXT    NOT NULL,
                embedding    vector({EMBEDDING_DIM})
            )
        """)
        cur.execute(f"""
            CREATE INDEX IF NOT EXISTS {TABLE_NAME}_company_idx
            ON {TABLE_NAME} (company)
        """)
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_chunks(chunks: list) -> int:
    """
    Embed and upsert filing chunks into PostgreSQL.
    Uses %s parameters (psycopg2 style) to avoid colon conflicts
    with SEC filing content like 'us-gaap:RetainedEarningsMember'.
    Returns number of rows inserted.
    """
    if not chunks:
        return 0

    _ensure_schema()

    texts      = [c.text for c in chunks]
    embeddings = _embed(texts)

    conn    = _get_conn()
    inserted = 0

    with conn.cursor() as cur:
        for chunk, embedding in zip(chunks, embeddings):
            chunk_id     = hashlib.md5(
                f"{chunk.source_url}:{chunk.chunk_index}".encode()
            ).hexdigest()
            embedding_str = _fmt_embedding(embedding)

            cur.execute(f"""
                INSERT INTO {TABLE_NAME}
                    (id, company, form_type, filed_at, section,
                     chunk_index, source_url, content, embedding)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                ON CONFLICT (id) DO NOTHING
            """, (
                chunk_id,
                chunk.company,
                chunk.form_type,
                chunk.filed_at,
                chunk.section,
                chunk.chunk_index,
                chunk.source_url,
                chunk.text,
                embedding_str,
            ))
            inserted += cur.rowcount

    conn.commit()
    return inserted


def query(
    query_text:     str,
    company_filter: str | None = None,
    top_k:          int        = TOP_K,
) -> list[dict]:
    """
    Semantic search using pgvector cosine distance operator (<=>) .
    Strict company filter — no cross-company result contamination.
    """
    _ensure_schema()

    query_embedding = _embed([query_text])[0]
    embedding_str   = _fmt_embedding(query_embedding)

    if company_filter:
        sql = f"""
            SELECT content, company, form_type, filed_at, section, source_url,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM {TABLE_NAME}
            WHERE company = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params = (embedding_str, company_filter, embedding_str, top_k)
    else:
        sql = f"""
            SELECT content, company, form_type, filed_at, section, source_url,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM {TABLE_NAME}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """
        params = (embedding_str, embedding_str, top_k)

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    except Exception as e:
        return []

    results = []
    for row in rows:
        content, company, form_type, filed_at, section, source_url, similarity = row
        if float(similarity) < MIN_SIMILARITY:
            continue
        results.append({
            "text":       content,
            "similarity": round(float(similarity), 3),
            "company":    company,
            "form_type":  form_type,
            "filed_at":   filed_at,
            "section":    section,
            "source_url": source_url,
        })

    return results


def format_rag_results(results: list[dict], query_text: str) -> str:
    """Identical output format to chroma_store — synthesis node unchanged."""
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
    """Return row count per company — mirrors chroma_store API."""
    try:
        _ensure_schema()
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT company, COUNT(*) FROM {TABLE_NAME} GROUP BY company ORDER BY COUNT(*) DESC"
            )
            companies = {row[0]: row[1] for row in cur.fetchall()}
        return {
            "total_chunks": total,
            "table":        TABLE_NAME,
            "companies":    companies,
        }
    except Exception as e:
        return {"error": str(e), "total_chunks": 0}