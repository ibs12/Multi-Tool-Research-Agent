"""
tests/test_rag.py
-----------------
Unit tests for the RAG pipeline.

Tests are pure / offline — no real vector DB, no HTTP calls.
All external dependencies (chroma_store, pgvector_store, sec_fetcher) are mocked.

Run with:
    pytest tests/test_rag.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── _parse_edgar_output (pure function) ───────────────────────────────────────

from tools.rag_search import _parse_edgar_output


SAMPLE_EDGAR_OUTPUT = """
SEC EDGAR FILINGS — Apple Inc. (AAPL)
======================================

[1] 10-K — Apple Inc.
Filed:  2024-11-01  |  Period: 2024-09-28
Accession: 0000320193-24-000123
URL: https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm

[2] 10-Q — Apple Inc.
Filed:  2025-02-06  |  Period: 2024-12-28
Accession: 0000320193-25-000010
URL: https://www.sec.gov/Archives/edgar/data/320193/000032019325000010/aapl-20241228.htm

[3] 8-K — Apple Inc.
Filed:  2025-01-30  |  Period: 2025-01-30
URL: https://www.sec.gov/Archives/edgar/data/320193/000032019325000999/aapl8k.htm
"""


def test_parse_edgar_extracts_10k_and_10q():
    filings = _parse_edgar_output(SAMPLE_EDGAR_OUTPUT)
    form_types = [f["form_type"] for f in filings]
    assert "10-K" in form_types
    assert "10-Q" in form_types


def test_parse_edgar_skips_8k():
    filings = _parse_edgar_output(SAMPLE_EDGAR_OUTPUT)
    form_types = [f["form_type"] for f in filings]
    assert "8-K" not in form_types


def test_parse_edgar_extracts_company_name():
    filings = _parse_edgar_output(SAMPLE_EDGAR_OUTPUT)
    companies = {f["company"] for f in filings}
    assert "Apple Inc." in companies


def test_parse_edgar_extracts_filed_date():
    filings = _parse_edgar_output(SAMPLE_EDGAR_OUTPUT)
    annual = next(f for f in filings if f["form_type"] == "10-K")
    assert annual["filed_at"] == "2024-11-01"


def test_parse_edgar_extracts_url():
    filings = _parse_edgar_output(SAMPLE_EDGAR_OUTPUT)
    annual = next(f for f in filings if f["form_type"] == "10-K")
    assert "sec.gov" in annual["url"]


def test_parse_edgar_empty_input():
    assert _parse_edgar_output("") == []


def test_parse_edgar_no_valid_filings():
    assert _parse_edgar_output("No filings found for this company.") == []


# ── run_rag_pipeline / ingest — exact per-filing ingest, never-raise ──────────

from tools.rag_search import run_rag_pipeline, ingest_filings_from_state, RAG_ERROR


# The two 10-K / 10-Q URLs that _parse_edgar_output extracts from the sample.
_AAPL_10K = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"
_AAPL_10Q = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000010/aapl-20241228.htm"
_EDGAR_RESULT = {"tool_name": "sec_edgar", "success": True, "output": SAMPLE_EDGAR_OUTPUT}


@patch("tools.rag_search.ingest_filings_from_state", new_callable=AsyncMock)
@patch("tools.rag_search.run_rag_query", new_callable=AsyncMock)
def test_pipeline_ingests_then_queries(mock_query, mock_ingest):
    mock_ingest.return_value = "RAG INGEST COMPLETE"
    mock_query.return_value = "SEC FILING RAG RESULTS: ..."

    out = asyncio.run(run_rag_pipeline("revenue", [_EDGAR_RESULT], company="Apple Inc."))

    mock_ingest.assert_awaited_once()
    assert "RAG INGEST COMPLETE" in out and "SEC FILING RAG RESULTS" in out


@patch("tools.rag_search.ingest_filings_from_state", new_callable=AsyncMock)
@patch("tools.rag_search.run_rag_query", new_callable=AsyncMock)
def test_pipeline_surfaces_query_error_at_position_0(mock_query, mock_ingest):
    # A query-phase error must be detectable by the node's startswith check —
    # it must not be buried behind the leading ingest status (issue #1).
    mock_ingest.return_value = "RAG INGEST: Skipped — all 1 filing(s) already indexed."
    mock_query.return_value = f"{RAG_ERROR} DBError: connection refused"

    out = asyncio.run(run_rag_pipeline("EPS", [_EDGAR_RESULT], company="Apple Inc."))

    assert out.startswith(RAG_ERROR)


@patch("tools.rag_search.ingest_filings_from_state", new_callable=AsyncMock)
def test_pipeline_never_raises(mock_ingest):
    # A store/fetch failure returns a tagged error, not an exception (issue #1).
    mock_ingest.side_effect = RuntimeError("vector store down")

    out = asyncio.run(run_rag_pipeline("EPS", [_EDGAR_RESULT], company="Apple Inc."))

    assert out.startswith(RAG_ERROR)


@patch("tools.rag_search.existing_source_urls", new_callable=AsyncMock)
@patch("tools.rag_search.ingest_chunks", new_callable=AsyncMock)
@patch("tools.rag_search.fetch_and_chunk")
def test_ingest_skips_filings_already_stored(mock_fetch, mock_ingest_chunks, mock_urls):
    # Both parsed filing URLs are already stored → no fetch, no re-embed (issue #3).
    mock_urls.return_value = {_AAPL_10K, _AAPL_10Q}

    out = asyncio.run(ingest_filings_from_state([_EDGAR_RESULT]))

    mock_fetch.assert_not_called()
    mock_ingest_chunks.assert_not_awaited()
    assert "already indexed" in out


@patch("tools.rag_search.existing_source_urls", new_callable=AsyncMock)
@patch("tools.rag_search.ingest_chunks", new_callable=AsyncMock)
@patch("tools.rag_search.fetch_and_chunk")
def test_ingest_fetches_filing_not_yet_stored(mock_fetch, mock_ingest_chunks, mock_urls):
    # Nothing stored → both filings fetched + ingested; a new filing URL is never
    # false-skipped by a fuzzy company match, and fresh filings are picked up (issue #3).
    mock_urls.return_value = set()
    mock_fetch.return_value = [MagicMock()]   # one chunk per filing
    mock_ingest_chunks.return_value = 1

    out = asyncio.run(ingest_filings_from_state([_EDGAR_RESULT]))

    assert mock_fetch.called
    mock_ingest_chunks.assert_awaited()
    assert "RAG INGEST COMPLETE" in out


# ── format_rag_results ─────────────────────────────────────────────────────────

from rag.chroma_store import format_rag_results


def test_format_rag_results_empty():
    out = format_rag_results([], "revenue growth")
    assert "No relevant passages" in out


def test_format_rag_results_single_result():
    results = [{
        "text": "Total net revenues were $391 billion for fiscal 2024.",
        "similarity": 0.87,
        "form_type": "10-K",
        "company": "Apple Inc.",
        "filed_at": "2024-11-01",
        "section": "Financial Statements",
        "source_url": "https://sec.gov/...",
    }]
    out = format_rag_results(results, "revenue")
    assert "Apple Inc." in out
    assert "10-K" in out
    assert "0.87" in out
    assert "$391 billion" in out


def test_format_rag_results_includes_query():
    out = format_rag_results([], "gross margin analysis")
    assert "gross margin analysis" in out
