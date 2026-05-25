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

from unittest.mock import MagicMock, patch

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


# ── run_rag_pipeline — skip-ingest when company already indexed ────────────────

from tools.rag_search import run_rag_pipeline


def _make_stats(company: str, count: int = 50) -> dict:
    return {"total_chunks": count, "companies": {company: count}}


@patch("tools.rag_search.collection_stats")
@patch("tools.rag_search.ingest_filings_from_state")
@patch("tools.rag_search.run_rag_query")
def test_skip_ingest_when_company_indexed(mock_query, mock_ingest, mock_stats):
    mock_stats.return_value = _make_stats("Apple Inc.")
    mock_query.return_value = "SEC FILING RAG RESULTS: ..."

    run_rag_pipeline("revenue growth", [], company="Apple Inc.")

    mock_ingest.assert_not_called()


@patch("tools.rag_search.collection_stats")
@patch("tools.rag_search.ingest_filings_from_state")
@patch("tools.rag_search.run_rag_query")
def test_skip_ingest_is_case_insensitive(mock_query, mock_ingest, mock_stats):
    # Stored as "Apple Inc." — query with "apple inc."
    mock_stats.return_value = _make_stats("Apple Inc.")
    mock_query.return_value = "SEC FILING RAG RESULTS: ..."

    run_rag_pipeline("revenue growth", [], company="apple inc.")

    mock_ingest.assert_not_called()


@patch("tools.rag_search.collection_stats")
@patch("tools.rag_search.ingest_filings_from_state")
@patch("tools.rag_search.run_rag_query")
def test_triggers_ingest_for_new_company(mock_query, mock_ingest, mock_stats):
    # Apple is indexed but we're asking about Microsoft
    mock_stats.return_value = _make_stats("Apple Inc.")
    mock_ingest.return_value = "RAG INGEST COMPLETE"
    mock_query.return_value = "SEC FILING RAG RESULTS: ..."

    run_rag_pipeline("revenue growth", [], company="Microsoft Corp.")

    mock_ingest.assert_called_once()


@patch("tools.rag_search.collection_stats")
@patch("tools.rag_search.ingest_filings_from_state")
@patch("tools.rag_search.run_rag_query")
def test_ingest_skipped_status_in_output(mock_query, mock_ingest, mock_stats):
    mock_stats.return_value = _make_stats("Apple Inc.", count=120)
    mock_query.return_value = "RAG query result here"

    result = run_rag_pipeline("EPS", [], company="Apple Inc.")

    assert "Skipped" in result
    assert "120" in result


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
