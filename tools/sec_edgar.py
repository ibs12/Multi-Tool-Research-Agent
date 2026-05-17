"""
tools/sec_edgar.py
------------------
SEC EDGAR full-text search tool for primary source financial filings.

Role in the agent:
  Web search gives us news and analyst opinion.
  SEC EDGAR gives us primary source data — actual 10-K/10-Q filings,
  earnings releases (8-K), and insider transactions directly from the
  company. This is the gold standard for financial figures.

Design decisions:
  - Uses EDGAR full-text search API (no API key required)
  - Filters to high-value form types: 10-K, 10-Q, 8-K, DEF 14A
  - Extracts filing metadata + snippet, not full document (keeps tokens low)
  - Falls back to company search if ticker lookup fails

Interview talking point:
  "I added SEC EDGAR as a primary source layer — web search gives analyst
   opinion but EDGAR gives us what the company actually reported. That's
   the difference between noise and signal for financial research."
"""

from __future__ import annotations

import re
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

# EDGAR full-text search — no auth required
EDGAR_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
EDGAR_FILING_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
EDGAR_BASE = "https://www.sec.gov"

# Form types ranked by research value
HIGH_VALUE_FORMS = ["10-K", "10-Q", "8-K", "DEF 14A", "SC 13G"]

MAX_RESULTS = 4
REQUEST_TIMEOUT = 10  # seconds


class SecEdgarTool:
    """
    Searches SEC EDGAR for filings related to a company.
    Uses the public EDGAR full-text search API.
    """

    def __init__(self):
        self._client = httpx.Client(
            timeout=REQUEST_TIMEOUT,
            headers={
                # EDGAR requires a descriptive User-Agent per SEC fair access policy
                "User-Agent": "FinancialResearchAgent/1.0 research@example.com",
                "Accept": "application/json",
            },
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    def search(self, company: str, max_results: int = MAX_RESULTS) -> str:
        """
        Search for recent SEC filings for a company.

        Tries ticker search first (most precise), falls back to
        company name search if ticker extraction fails.
        """
        # Extract ticker if present e.g. "Apple Inc. (AAPL)" -> "AAPL"
        ticker = _extract_ticker(company)
        company_clean = _strip_ticker(company)

        filings = []

        # Strategy 1: EDGAR company search by ticker or name
        if ticker:
            filings = self._search_by_entity(ticker, max_results)

        if not filings:
            filings = self._search_by_entity(company_clean, max_results)

        # Strategy 2: Full-text search fallback
        if not filings:
            filings = self._fulltext_search(company_clean, max_results)

        return _format_results(company, filings)

    def _search_by_entity(self, query: str, max_results: int) -> list[dict]:
        """Search EDGAR company database by ticker or name."""
        try:
            resp = self._client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": f'"{query}"',
                    "dateRange": "custom",
                    "startdt": "2023-01-01",
                    "forms": ",".join(HIGH_VALUE_FORMS),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            return [_parse_hit(h) for h in hits[:max_results]]
        except Exception:
            return []

    def _fulltext_search(self, company: str, max_results: int) -> list[dict]:
        """Full-text search across all EDGAR filings."""
        try:
            resp = self._client.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": company,
                    "forms": "10-K,10-Q,8-K",
                    "dateRange": "custom",
                    "startdt": "2024-01-01",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            return [_parse_hit(h) for h in hits[:max_results]]
        except Exception:
            return []


# -- Helpers ------------------------------------------------------------------

def _extract_ticker(name: str) -> str:
    """Extract ticker from 'Company Name (TICK)' format."""
    match = re.search(r'\(([A-Z]{1,5})\)', name)
    return match.group(1) if match else ""


def _strip_ticker(name: str) -> str:
    """Remove ticker suffix and legal suffixes."""
    name = re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', name).strip()
    for suffix in [" Inc.", " Inc", " Corp.", " Corp", " Ltd", " LLC", " plc"]:
        name = name.replace(suffix, "")
    return name.strip()


def _parse_hit(hit: dict) -> dict:
    """Extract relevant fields from an EDGAR search hit."""
    src = hit.get("_source", {})
    return {
        "form_type": src.get("form_type", "Unknown"),
        "company_name": src.get("entity_name", "Unknown"),
        "filed_at": src.get("file_date", "Unknown"),
        "period": src.get("period_of_report", ""),
        "description": src.get("file_description", ""),
        "url": f"{EDGAR_BASE}{src.get('file_path', '')}",
        "snippet": _clean_snippet(hit.get("highlight", {}).get("file_text", [""])[0]),
    }


def _clean_snippet(raw: str) -> str:
    """Strip HTML tags from EDGAR search snippets."""
    clean = re.sub(r'<[^>]+>', '', raw)
    return clean.strip()[:400]


def _format_results(query: str, filings: list[dict]) -> str:
    """Format filing results as clean text for agent state."""
    lines = [
        f"SEC EDGAR RESULTS for: '{query}'",
        "=" * 60,
    ]

    if not filings:
        lines += [
            "No recent filings found via EDGAR search.",
            "Suggest cross-referencing at: https://www.sec.gov/cgi-bin/browse-edgar",
        ]
        return "\n".join(lines)

    for i, f in enumerate(filings, 1):
        lines += [
            f"[{i}] {f['form_type']} — {f['company_name']}",
            f"    Filed:  {f['filed_at']}  |  Period: {f['period']}",
            f"    URL:    {f['url']}",
        ]
        if f["snippet"]:
            lines.append(f"    Excerpt: {f['snippet']}")
        lines.append("")

    return "\n".join(lines)


# -- Convenience entry point --------------------------------------------------

def run_sec_search(query: str) -> str:
    """
    Top-level entry point called by the LangGraph SEC node.
    Never raises — always returns a string.
    """
    try:
        tool = SecEdgarTool()
        return tool.search(query)
    except Exception as e:
        return f"[SEC EDGAR Error] {type(e).__name__}: {e}"