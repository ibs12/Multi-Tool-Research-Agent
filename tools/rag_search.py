"""
tools/rag_search.py
-------------------
RAG tool that ingests SEC filing documents and answers semantic queries.

Two-phase operation:
  Phase 1 (ingest): Given a list of filing URLs from sec_edgar tool,
    fetch each document, chunk it, embed it, and store in the vector DB.

  Phase 2 (query): Semantic search over stored chunks, returning cited
    passages for synthesis to include as [SEC Filing] sources.

Backend is selected automatically:
  PGVECTOR_URL set  →  PostgreSQL + pgvector (async via asyncpg)
  PGVECTOR_URL unset →  ChromaDB (local dev, wrapped async)
"""

from __future__ import annotations

import asyncio
import re
from rag.sec_fetcher import fetch_and_chunk
from rag.rag_backend import ingest_chunks, query, format_rag_results, collection_stats


# -- Ingest phase -------------------------------------------------------------

async def ingest_filings_from_state(tool_results: list[dict]) -> str:
    """
    Parse SEC EDGAR tool results from agent state, fetch the filing
    documents concurrently, and ingest into the vector store.
    Returns a status string describing what was ingested.
    """
    edgar_results = [
        r for r in tool_results
        if r.get("tool_name") == "sec_edgar" and r.get("success")
    ]

    if not edgar_results:
        return "RAG INGEST: No SEC EDGAR results found in state to ingest."

    filings_to_fetch = []
    for result in edgar_results:
        filings_to_fetch.extend(_parse_edgar_output(result["output"]))

    if not filings_to_fetch:
        return "RAG INGEST: Could not parse filing URLs from SEC EDGAR output."

    target_filings = filings_to_fetch[:3]   # cap at 3 to control latency

    # Fetch and chunk all filings concurrently (sec_fetcher uses urllib — runs in threads)
    all_chunks = await asyncio.gather(*[
        asyncio.to_thread(
            fetch_and_chunk,
            url=f["url"],
            form_type=f["form_type"],
            company=f["company"],
            filed_at=f["filed_at"],
        )
        for f in target_filings
    ])

    total_chunks = 0
    ingested: list[str] = []
    skipped:  list[str] = []

    for filing, chunks in zip(target_filings, all_chunks):
        if chunks:
            n = await ingest_chunks(chunks)
            total_chunks += n
            ingested.append(f"{filing['form_type']} ({filing['filed_at']})")
        else:
            skipped.append(filing["url"][:60])

    lines = [
        "RAG INGEST COMPLETE",
        "=" * 40,
        f"Filings ingested: {len(ingested)}",
        f"Total chunks stored: {total_chunks}",
    ]
    if ingested:
        lines.append(f"Documents: {', '.join(ingested)}")
    if skipped:
        lines.append(f"Skipped (fetch failed): {len(skipped)}")

    return "\n".join(lines)


def _parse_edgar_output(edgar_text: str) -> list[dict]:
    """
    Parse the formatted SEC EDGAR output string to extract filing metadata.
    Handles the format produced by tools/sec_edgar.py _format_results().
    """
    filings = []
    blocks = edgar_text.split("\n\n")
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue

        form_type = company = filed_at = url = ""

        for line in lines:
            header_match = re.match(r'\[\d+\]\s+(\S+)\s+—\s+(.+)', line)
            if header_match:
                form_type = header_match.group(1).strip()
                company   = header_match.group(2).strip()

            filed_match = re.search(r'Filed:\s+([\d-]+)', line)
            if filed_match:
                filed_at = filed_match.group(1)

            if line.startswith("URL:"):
                url = line.replace("URL:", "").strip()

        if url and form_type in ("10-K", "10-Q") and company:
            filings.append({"form_type": form_type, "company": company,
                             "filed_at": filed_at, "url": url})

    return filings


# -- Query phase --------------------------------------------------------------

async def run_rag_query(query_text: str, company: str | None = None) -> str:
    """Semantic search over ingested SEC filing chunks."""
    try:
        results = await query(query_text, company_filter=company, top_k=5)
        return format_rag_results(results, query_text)
    except Exception as e:
        return f"[RAG Error] {type(e).__name__}: {e}"


# -- Combined entry point (ingest + query) ------------------------------------

async def run_rag_pipeline(query_text: str, tool_results: list[dict], company: str = "") -> str:
    """
    Full RAG pipeline: ingest SEC filings if needed, then semantic query.

    Ingest is skipped when the vector store already holds chunks for this
    company — avoids redundant network fetches and works correctly when
    rag_search runs in the same dispatcher batch as sec_edgar (both see
    the same state snapshot, so sec_edgar results aren't in tool_results yet).
    """
    import re as _re

    stats    = await collection_stats()
    existing = stats.get("companies", {})
    company_key = company.strip()

    def _first_word(s: str) -> str:
        return _re.sub(r'[^a-z]', '', s.lower().split()[0]) if s.strip() else ''

    target_word = _first_word(company_key)
    already_indexed = any(
        target_word and target_word in _re.sub(r'[^a-z]', '', stored.lower())
        for stored in existing
    ) if target_word else stats.get("total_chunks", 0) > 0

    if already_indexed:
        chunk_count = existing.get(company_key) or next(
            (v for k, v in existing.items() if k.lower() == company_key.lower()), 0
        )
        ingest_status = (
            f"RAG INGEST: Skipped — {chunk_count} chunks for '{company_key}' already indexed."
        )
    else:
        ingest_status = await ingest_filings_from_state(tool_results)

    query_result = await run_rag_query(query_text, company or None)
    return f"{ingest_status}\n\n{query_result}"
