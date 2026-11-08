"""
tests/test_sec_edgar_flags.py
-----------------------------
The SEC tool must surface filings that bear on whether reported figures can be
relied on. Before this, NT/amendment forms were filtered out and 8-Ks were
listed without their item codes — every should-escalate boundary case was
cleared because compliance never saw the red flag (docs/eval/2026-10-10).
Offline: EDGAR is replaced by a fixture shaped like the real submissions JSON.
"""

from __future__ import annotations

import asyncio
from datetime import date

from tools import sec_edgar as se
from tools.rag_search import _parse_edgar_output

TODAY = date(2026, 11, 8)


def _subs(rows):
    """rows: (form, filed, report, items, accession, doc) — newest first, like EDGAR."""
    keys = ["form", "filingDate", "reportDate", "items", "accessionNumber", "primaryDocument"]
    return {"name": "ARCHER-DANIELS-MIDLAND CO",
            "filings": {"recent": {k: [r[i] for r in rows] for i, k in enumerate(keys)}}}


ADM = _subs([
    ("4", "2026-10-01", "2026-09-30", "", "0001-26-1", "f4.xml"),                 # insider: never a flag
    ("10-Q", "2026-07-29", "2026-06-30", "", "0001-26-2", "q.htm"),
    ("8-K", "2026-07-29", "2026-07-29", "2.02,9.01", "0001-26-3", "e.htm"),       # earnings: not a flag
    ("10-K/A", "2024-11-18", "2023-12-31", "", "0001-24-4", "ka.htm"),
    ("8-K", "2024-11-05", "2024-11-04", "4.02,9.01", "0001-24-5", "nr.htm"),
    ("NT 10-K", "2024-03-01", "2023-12-31", "", "0001-24-6", "nt.htm"),
    ("8-K", "2022-06-01", "2022-06-01", "4.01", "0001-22-7", "old.htm"),          # outside lookback
])


def test_red_flags_are_found_across_the_whole_history():
    flags = se._red_flags(ADM, "7084", today=TODAY)
    labels = [f["label"] for f in flags]
    assert labels == [
        "10-K/A — Amended annual report — check whether reported figures changed",
        "8-K Item 4.02 — Non-reliance on previously issued financial statements",
        "NT 10-K — Late annual report (notification of late filing)",
    ]
    assert flags[1]["url"].endswith("/7084/0001245/nr.htm")


def test_only_flagged_8k_items_count():
    flags = se._red_flags(_subs([("8-K", "2026-01-05", "2026-01-02", "5.02,8.01", "a", "d")]), "1", today=TODAY)
    assert flags == []


def test_flags_come_first_so_the_truncated_digest_keeps_them():
    filings = [{"form_type": "10-K", "company_name": "ADM", "filed_at": "2026-02-17",
                "period": "2025-12-31", "url": "https://sec.gov/x/10k.htm", "snippet": ""}]
    out = se._format_results("ADM", filings, se._red_flags(ADM, "7084", today=TODAY))
    assert out.index("REGULATORY FLAGS") < out.index("[1] 10-K")
    assert "8-K Item 4.02" in out[:2500]                   # inside agents.py's per-tool cap
    assert "(event 2024-11-04)" in out and "(for period ending 2023-12-31)" in out


def test_rag_ingests_exactly_the_same_filings_with_flags_present():
    filings = [{"form_type": "10-K", "company_name": "ADM", "filed_at": "2026-02-17",
                "period": "2025-12-31", "url": "https://sec.gov/x/10k.htm", "snippet": ""},
               {"form_type": "8-K", "company_name": "ADM", "filed_at": "2026-07-29",
                "period": "2026-07-29", "url": "https://sec.gov/x/8k.htm", "snippet": ""}]
    with_flags = se._format_results("ADM", filings, se._red_flags(ADM, "7084", today=TODAY))
    without = se._format_results("ADM", filings, None)
    assert _parse_edgar_output(with_flags) == _parse_edgar_output(without)
    assert [f["url"] for f in _parse_edgar_output(with_flags)] == ["https://sec.gov/x/10k.htm"]


def test_absence_is_stated_and_an_unchecked_filer_is_not_called_clean():
    assert "none found." in se._format_results("AAPL", [], [])
    assert "REGULATORY FLAGS" not in se._format_results("Unknown Co", [], None)


def test_search_fetches_flags_via_the_ticker_path(monkeypatch):
    async def fake_get_json(url, params=None):
        if url == se.TICKER_MAP_URL:
            return {"0": {"ticker": "ADM", "cik_str": 7084}}
        return ADM
    monkeypatch.setattr(se, "_get_json", fake_get_json)
    monkeypatch.setattr(se, "date", type("D", (), {"today": staticmethod(lambda: TODAY)}))
    out = asyncio.run(se.run_sec_search("Archer-Daniels-Midland (ADM)"))
    assert "8-K Item 4.02" in out and "[1] 10-Q" in out
