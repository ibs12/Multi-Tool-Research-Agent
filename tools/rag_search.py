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
  PGVECTOR_URL set  →  PostgreSQL + pgvector
  PGVECTOR_URL unset →  ChromaDB (local dev)
"""

from __future__ import annotations

import re
from rag.sec_fetcher import fetch_and_chunk
from rag.rag_backend import ingest_chunks, query, format_rag_results, collection_stats


# -- Ingest phase -------------------------------------------------------------

def ingest_filings_from_state(tool_results: list[dict]) -> str:
    """
    Parse SEC EDGAR tool results from agent state, fetch the filing
    documents, and ingest them into ChromaDB.

    Called by the rag_ingest_node before any semantic queries.
    Returns a status string describing what was ingested.
    """
    # Extract SEC EDGAR results from accumulated tool results
    edgar_results = [
        r for r in tool_results
        if r.get("tool_name") == "sec_edgar" and r.get("success")
    ]

    if not edgar_results:
        return "RAG INGEST: No SEC EDGAR results found in state to ingest."

    # Parse filing URLs and metadata from the formatted EDGAR output
    filings_to_fetch = []
    for result in edgar_results:
        filings_to_fetch.extend(_parse_edgar_output(result["output"]))

    if not filings_to_fetch:
        return "RAG INGEST: Could not parse filing URLs from SEC EDGAR output."

    # Fetch, chunk, and ingest each filing
    total_chunks = 0
    ingested     = []
    skipped      = []

    for filing in filings_to_fetch[:3]:   # cap at 3 to control latency
        chunks = fetch_and_chunk(
            url=filing["url"],
            form_type=filing["form_type"],
            company=filing["company"],
            filed_at=filing["filed_at"],
        )
        if chunks:
            n = ingest_chunks(chunks)
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

    # Pattern: "[N] FORM_TYPE — Company Name"
    # followed by "Filed: DATE | Period: DATE"
    # followed by "URL: https://..."

    blocks = edgar_text.split("\n\n")
    for block in blocks:
        lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue

        form_type   = ""
        company     = ""
        filed_at    = ""
        url         = ""

        for line in lines:
            # Header line: "[1] 10-K — Apple Inc."
            header_match = re.match(r'\[\d+\]\s+(\S+)\s+—\s+(.+)', line)
            if header_match:
                form_type = header_match.group(1).strip()
                company   = header_match.group(2).strip()

            # Filed line: "Filed:  2026-01-29  |  Period: 2025-12-31"
            filed_match = re.search(r'Filed:\s+([\d-]+)', line)
            if filed_match:
                filed_at = filed_match.group(1)

            # URL line
            if line.startswith("URL:"):
                url = line.replace("URL:", "").strip()

        # Only include 10-K and 10-Q — most information-dense
        if url and form_type in ("10-K", "10-Q") and company:
            filings.append({
                "form_type": form_type,
                "company":   company,
                "filed_at":  filed_at,
                "url":       url,
            })

    return filings


# -- Query phase --------------------------------------------------------------

def run_rag_query(query_text: str, company: str | None = None) -> str:
    """
    Semantic search over ingested SEC filing chunks.
    Called by the rag_search_node in agent/nodes/tools.py.
    """
    try:
        results = query(query_text, company_filter=company, top_k=5)
        return format_rag_results(results, query_text)
    except Exception as e:
        return f"[RAG Error] {type(e).__name__}: {e}"


# -- Combined entry point (ingest + query) ------------------------------------

def run_rag_pipeline(query_text: str, tool_results: list[dict], company: str = "") -> str:
    """
    Full RAG pipeline: ingest SEC filings if needed, then semantic query.

    Ingest is skipped when the vector store already holds chunks for this
    company — avoids redundant network fetches and works correctly when
    rag_search runs in the same dispatcher batch as sec_edgar (both see
    the same state snapshot, so sec_edgar results aren't in tool_results yet).
    A fresh ingest is triggered when querying a company not yet indexed.
    """
    import re as _re

    stats = collection_stats()
    existing = stats.get("companies", {})
    company_key = company.strip()

    # Compare by first significant word only (handles "CITIGROUP INC" vs "Citigroup Inc.")
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
        ingest_status = ingest_filings_from_state(tool_results)

    query_result = run_rag_query(query_text, company or None)
    return f"{ingest_status}\n\n{query_result}"