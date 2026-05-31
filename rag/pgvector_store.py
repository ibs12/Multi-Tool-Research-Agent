"""
rag/pgvector_store.py
---------------------
PostgreSQL + pgvector backend — async rewrite using asyncpg.

asyncpg does not have a built-in codec for pgvector's `vector` type, so
embedding values are embedded directly as SQL string literals rather than
bound parameters.  This is safe because _fmt_embedding() only emits digits,
commas, dots, and brackets — no SQL injection surface.

Interview talking point:
    "asyncpg can't bind pgvector's vector type as a parameter, so I format
     the embedding as a '[f1,f2,...]'::vector literal in the SQL string.
     The values are pure floats from numpy, so there's zero injection risk.
     The payoff is that every DB call is non-blocking: asyncio.gather runs
     all tool coroutines concurrently, and asyncpg pools connections across
     requests without spawning threads."
"""

from __future__ import annotations

import asyncio
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

# ── Module-level singletons ───────────────────────────────────────────────────
_pool            = None
_pool_lock       = asyncio.Lock()   # safe at module level in Python 3.10+
_schema_ready    = False
_schema_lock     = asyncio.Lock()
_embedding_model = None


# ── Connection pool ───────────────────────────────────────────────────────────

async def _get_pool():
    global _pool
    async with _pool_lock:
        if _pool is None or getattr(_pool, "_closed", False):
            try:
                import asyncpg
            except ImportError:
                raise ImportError("Run: pip install asyncpg")
            _pool = await asyncpg.create_pool(
                PGVECTOR_URL,
                min_size=1,
                max_size=5,
                command_timeout=30,
            )
    return _pool


# ── Embedding (sentence-transformers is sync — runs in thread) ────────────────

def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            raise ImportError("Run: pip install sentence-transformers")
    return _embedding_model


def _embed_sync(texts: list[str]) -> list[list[float]]:
    return _get_embedding_model().encode(texts, batch_size=32, show_progress_bar=False).tolist()


async def _embed(texts: list[str]) -> list[list[float]]:
    """Wrap CPU-bound embedding in a thread so the event loop stays free."""
    return await asyncio.to_thread(_embed_sync, texts)


def _fmt_embedding(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _vec_literal(vec: list[float]) -> str:
    """
    Format as a pgvector SQL literal for direct substitution in query strings.
    Safe: vec contains only Python floats → only digits/dots/commas/brackets.
    asyncpg can't bind the custom vector type as a $N parameter, so we bypass
    parameter binding for embedding values only.
    """
    return f"'{_fmt_embedding(vec)}'::vector"


# ── Schema ────────────────────────────────────────────────────────────────────

async def _ensure_schema():
    global _schema_ready
    if _schema_ready:
        return
    async with _schema_lock:
        if _schema_ready:
            return
        pool = await _get_pool()
        async with pool.acquire() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(f"""
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
            await conn.execute(f"""
                CREATE INDEX IF NOT EXISTS {TABLE_NAME}_company_idx
                ON {TABLE_NAME} (company)
            """)
        _schema_ready = True


# ── Public API ────────────────────────────────────────────────────────────────

async def ingest_chunks(chunks: list) -> int:
    """Embed and upsert filing chunks into PostgreSQL. Returns rows inserted."""
    if not chunks:
        return 0

    await _ensure_schema()

    texts      = [c.text for c in chunks]
    embeddings = await _embed(texts)

    pool     = await _get_pool()
    inserted = 0

    async with pool.acquire() as conn:
        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = hashlib.md5(
                f"{chunk.source_url}:{chunk.chunk_index}".encode()
            ).hexdigest()
            emb = _vec_literal(embedding)

            status = await conn.execute(f"""
                INSERT INTO {TABLE_NAME}
                    (id, company, form_type, filed_at, section,
                     chunk_index, source_url, content, embedding)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, {emb})
                ON CONFLICT (id) DO NOTHING
            """,
                chunk_id, chunk.company, chunk.form_type, chunk.filed_at,
                chunk.section, chunk.chunk_index, chunk.source_url, chunk.text,
            )
            # asyncpg returns "INSERT 0 N" — last token is rows affected
            inserted += int(status.split()[-1])

    return inserted


async def query(
    query_text:     str,
    company_filter: str | None = None,
    top_k:          int        = TOP_K,
) -> list[dict]:
    """
    Semantic search using pgvector cosine distance (<=>).

    Company filter matches on first significant word case-insensitively.
    Falls back to unfiltered search if the filter yields no rows.
    """
    import re as _re

    await _ensure_schema()

    query_embedding = (await _embed([query_text]))[0]
    emb             = _vec_literal(query_embedding)

    base = f"""
        SELECT content, company, form_type, filed_at, section, source_url,
               1 - (embedding <=> {emb}) AS similarity
        FROM {TABLE_NAME}
    """

    pool = await _get_pool()
    rows = []

    if company_filter:
        first_word = _re.sub(r'[^a-z]', '', company_filter.lower().split()[0])
        if first_word:
            try:
                async with pool.acquire() as conn:
                    rows = list(await conn.fetch(
                        base + f" WHERE LOWER(company) LIKE $1"
                               f" ORDER BY embedding <=> {emb} LIMIT $2",
                        f'%{first_word}%', top_k,
                    ))
            except Exception:
                rows = []

    if not rows:
        try:
            async with pool.acquire() as conn:
                rows = list(await conn.fetch(
                    base + f" ORDER BY embedding <=> {emb} LIMIT $1",
                    top_k,
                ))
        except Exception:
            rows = []

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
    """Pure formatting function — stays sync."""
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


async def collection_stats() -> dict:
    """Row count per company — mirrors the former psycopg2 API."""
    try:
        await _ensure_schema()
        pool = await _get_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            rows  = await conn.fetch(
                f"SELECT company, COUNT(*) FROM {TABLE_NAME}"
                f" GROUP BY company ORDER BY COUNT(*) DESC"
            )
            companies = {row[0]: row[1] for row in rows}
        return {"total_chunks": total, "table": TABLE_NAME, "companies": companies}
    except Exception as e:
        return {"error": str(e), "total_chunks": 0}
