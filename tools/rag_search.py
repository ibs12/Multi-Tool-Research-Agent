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
from rag.rag_backend import ingest_chunks, query, format_rag_results, existing_source_urls

# Error sentinel — a RAG call "failed" iff its output starts with this. Defined
# once here so the tool and its node can't drift apart (see ADR-0003).
RAG_ERROR = "[RAG Error]"


# -- Ingest phase -------------------------------------------------------------

async def ingest_filings_from_state(tool_results: list[dict]) -> str:
    """
    Parse SEC EDGAR tool results from agent state, then fetch/chunk/ingest only
    the filings we don't already hold — an exact per-filing source_url check.

    A new filing (new URL) is always ingested (no staleness); a filing already
    stored is skipped before fetching (no redundant download/embed), with no
    fuzzy company-name match that could false-skip a different company.
    """
    edgar_results = [
        r for r in tool_results
        if r.get("tool_name") == "sec_edgar" and r.get("success")
    ]

    if not edgar_results:
        return "RAG INGEST: No SEC EDGAR results found in state to ingest."

    filings = []
    for result in edgar_results:
        filings.extend(_parse_edgar_output(result["output"]))

    if not filings:
        return "RAG INGEST: Could not parse filing URLs from SEC EDGAR output."

    filings = filings[:3]   # cap at 3 to control latency

    # Exact per-filing check: fetch/embed only filings whose source_url is not
    # already stored. The store is idempotent by chunk id, so this purely avoids
    # the redundant network + embedding cost of re-ingesting a known filing.
    have = await existing_source_urls()
    todo = [f for f in filings if f["url"] not in have]
    already = len(filings) - len(todo)

    if not todo:
        return f"RAG INGEST: Skipped — all {len(filings)} filing(s) already indexed."

    # Fetch and chunk the new filings concurrently (sec_fetcher uses urllib — runs in threads)
    all_chunks = await asyncio.gather(*[
        asyncio.to_thread(
            fetch_and_chunk,
            url=f["url"],
            form_type=f["form_type"],
            company=f["company"],
            filed_at=f["filed_at"],
        )
        for f in todo
    ])

    total_chunks = 0
    ingested: list[str] = []
    skipped:  list[str] = []

    for filing, chunks in zip(todo, all_chunks):
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
    if already:
        lines.append(f"Already indexed (fetch skipped): {already}")
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
        return f"{RAG_ERROR} {type(e).__name__}: {e}"


# -- Combined entry point (ingest + query) ------------------------------------

async def run_rag_pipeline(query_text: str, tool_results: list[dict], company: str = "") -> str:
    """
    Full RAG pipeline: ingest any not-yet-indexed SEC filings, then semantic query.

    Never raises: the whole pipeline is wrapped so a store/fetch failure returns
    a tagged error string rather than propagating out of the tool node and
    crashing the graph (see ADR-0003). A query-phase error is surfaced at
    position 0 so the node's success check can see it regardless of the leading
    ingest status.
    """
    try:
        ingest_status = await ingest_filings_from_state(tool_results)
        query_result  = await run_rag_query(query_text, company or None)
        if query_result.startswith(RAG_ERROR):
            return query_result
        return f"{ingest_status}\n\n{query_result}"
    except Exception as e:
        return f"{RAG_ERROR} {type(e).__name__}: {e}"
