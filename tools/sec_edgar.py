"""
tools/sec_edgar.py
------------------
SEC EDGAR filing search tool for primary source financial data.

Uses two complementary EDGAR APIs:
  1. EDGAR company tickers JSON  -- maps ticker -> CIK number
  2. EDGAR submissions API       -- returns structured filing history per CIK
  3. EDGAR full-text search      -- fallback for unmatched tickers

Rewritten to use httpx.AsyncClient so all HTTP calls are non-blocking.
"""

from __future__ import annotations

import re

import httpx

EDGAR_BASE       = "https://www.sec.gov"
EFTS_SEARCH      = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
TICKER_MAP_URL   = "https://www.sec.gov/files/company_tickers.json"

HIGH_VALUE_FORMS = {"10-K", "10-Q", "8-K", "DEF 14A"}
MAX_RESULTS      = 5
REQUEST_TIMEOUT  = 12

HEADERS = {
    "User-Agent": "FinancialResearchAgent/1.0 research@example.com",
    "Accept":     "application/json",
}

# Error sentinel — imported by sec_edgar_node (ADR-0003).
SEC_EDGAR_ERROR = "[SEC EDGAR Error]"


# -- Async HTTP helper --------------------------------------------------------

async def _get_json(url: str, params: dict | None = None) -> dict:
    """GET request returning parsed JSON. Raises on HTTP/network errors."""
    async with httpx.AsyncClient(headers=HEADERS, timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


# -- Main tool ----------------------------------------------------------------

class SecEdgarTool:

    async def search(self, company: str, max_results: int = MAX_RESULTS) -> str:
        ticker        = _extract_ticker(company)
        company_clean = _strip_ticker(company)
        filings: list[dict] = []

        # Strategy 1: ticker → CIK → submissions API (structured, reliable)
        if ticker:
            cik = await self._ticker_to_cik(ticker)
            if cik:
                filings = await self._filings_from_submissions(cik, max_results)

        # Strategy 2: full-text search by ticker
        if not filings and ticker:
            filings = await self._efts_search(ticker, max_results)

        # Strategy 3: full-text search by company name
        if not filings:
            filings = await self._efts_search(company_clean, max_results)

        return _format_results(company, filings)

    # -- Strategy 1: Submissions API ------------------------------------------

    async def _ticker_to_cik(self, ticker: str) -> str:
        try:
            data  = await _get_json(TICKER_MAP_URL)
            upper = ticker.upper()
            for entry in data.values():
                if str(entry.get("ticker", "")).upper() == upper:
                    return str(entry["cik_str"]).zfill(10)
        except Exception:
            pass
        return ""

    async def _filings_from_submissions(self, cik: str, max_results: int) -> list[dict]:
        try:
            data         = await _get_json(f"{SUBMISSIONS_BASE}/CIK{cik}.json")
            company_name = data.get("name", "Unknown")
            recent       = data.get("filings", {}).get("recent", {})

            forms        = recent.get("form", [])
            dates        = recent.get("filingDate", [])
            periods      = recent.get("reportDate", [])
            accessions   = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])

            filings: list[dict] = []
            for i, form in enumerate(forms):
                if form not in HIGH_VALUE_FORMS:
                    continue
                acc     = accessions[i].replace("-", "")   if i < len(accessions)   else ""
                doc     = primary_docs[i]                   if i < len(primary_docs) else ""
                cik_int = int(cik)
                filing_url = (
                    f"{EDGAR_BASE}/Archives/edgar/data/{cik_int}/{acc}/{doc}"
                    if acc and doc else
                    f"{EDGAR_BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik_int}&type={form}"
                )
                filings.append({
                    "form_type":    form,
                    "company_name": company_name,
                    "filed_at":     dates[i]   if i < len(dates)   else "Unknown",
                    "period":       periods[i] if i < len(periods) else "",
                    "url":          filing_url,
                    "snippet":      "",
                })
                if len(filings) >= max_results:
                    break
            return filings
        except Exception:
            return []

    # -- Strategy 2/3: EFTS full-text search fallback -------------------------

    async def _efts_search(self, query: str, max_results: int) -> list[dict]:
        try:
            data = await _get_json(EFTS_SEARCH, {
                "q":         f'"{query}"',
                "dateRange": "custom",
                "startdt":   "2023-01-01",
                "forms":     ",".join(HIGH_VALUE_FORMS),
            })
            hits = data.get("hits", {}).get("hits", [])
            return [_parse_efts_hit(h) for h in hits[:max_results]]
        except Exception:
            return []


# -- Parsers ------------------------------------------------------------------

def _parse_efts_hit(hit: dict) -> dict:
    src         = hit.get("_source", {})
    file_path   = src.get("file_path", "")
    highlights  = hit.get("highlight", {}).get("file_text", [""])
    snippet     = _clean_snippet(highlights[0] if highlights else "")

    form_type    = src.get("form_type")   or src.get("file_type",      "Unknown")
    company_name = src.get("entity_name") or (src.get("display_names", ["Unknown"])[0])
    filed_at     = src.get("file_date")   or src.get("period_of_report", "Unknown")

    return {
        "form_type":    form_type,
        "company_name": company_name,
        "filed_at":     filed_at,
        "period":       src.get("period_of_report", ""),
        "url":          f"{EDGAR_BASE}{file_path}" if file_path else EDGAR_BASE,
        "snippet":      snippet,
    }


def _clean_snippet(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", raw).strip()[:400]


# -- String helpers -----------------------------------------------------------

def _extract_ticker(name: str) -> str:
    m = re.search(r"\(([A-Z]{1,5})\)", name)
    return m.group(1) if m else ""


def _strip_ticker(name: str) -> str:
    name = re.sub(r"\s*\([A-Z]{1,5}\)\s*$", "", name).strip()
    for sfx in [" Inc.", " Inc", " Corp.", " Corp", " Ltd", " LLC", " plc"]:
        name = name.replace(sfx, "")
    return name.strip()


# -- Formatter ----------------------------------------------------------------

def _format_results(query: str, filings: list[dict]) -> str:
    lines = [f"SEC EDGAR RESULTS for: '{query}'", "=" * 60]

    if not filings:
        lines += [
            "No recent filings found.",
            f"Search manually: {EDGAR_BASE}/cgi-bin/browse-edgar",
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


# -- Entry point --------------------------------------------------------------

async def run_sec_search(query: str) -> str:
    """Called by the LangGraph SEC EDGAR node. Never raises."""
    try:
        return await SecEdgarTool().search(query)
    except Exception as e:
        return f"{SEC_EDGAR_ERROR} {type(e).__name__}: {e}"
