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

import asyncio
import html as _html
import re
from datetime import date, timedelta

import httpx

EDGAR_BASE       = "https://www.sec.gov"
EFTS_SEARCH      = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
TICKER_MAP_URL   = "https://www.sec.gov/files/company_tickers.json"

HIGH_VALUE_FORMS = {"10-K", "10-Q", "8-K", "DEF 14A"}
MAX_RESULTS      = 5

# ── Regulatory red flags ──────────────────────────────────────────────────────
# Filings that bear on whether a company's reported figures can be relied on.
# The compliance policy escalates on "a regulatory red flag", but until these
# were surfaced the agent could not see one: NT/amendment forms were filtered
# out and an 8-K was listed only as "8-K", its item codes dropped. Every
# should-escalate boundary case was cleared as a result (docs/eval/2026-10-10).
#
# They are scanned across the company's whole recent history, not the five
# newest filings: a non-reliance notice from 2024 is long buried by 2026.
RED_FLAG_8K_ITEMS = {
    "4.02": "Non-reliance on previously issued financial statements",
    "4.01": "Change in the company's certifying accountant (auditor)",
    "3.01": "Notice of delisting or failure to satisfy a listing standard",
}
RED_FLAG_FORMS = {
    "NT 10-K": "Late annual report (notification of late filing)",
    "NT 10-Q": "Late quarterly report (notification of late filing)",
    "10-K/A": "Amended annual report — check whether reported figures changed",
}
FLAG_LOOKBACK_DAYS = 3 * 365          # the fiscal years the brief reports on
MAX_FLAGS          = 8
REQUEST_TIMEOUT  = 12

HEADERS = {
    "User-Agent": "FinancialResearchAgent/1.0 research@example.com",
    "Accept":     "application/json",
}

# Error sentinel — imported by sec_edgar_node (ADR-0003).
SEC_EDGAR_ERROR = "[SEC EDGAR Error]"


# -- Async HTTP helper --------------------------------------------------------

async def _get_text(url: str) -> str:
    """GET a filing document as plain text (tags stripped). Raises on errors."""
    async with httpx.AsyncClient(headers={**HEADERS, "Accept": "text/html"},
                                 timeout=REQUEST_TIMEOUT) as client:
        response = await client.get(url)
        response.raise_for_status()
        text = _html.unescape(re.sub(r"<[^>]+>", " ", response.text))
        return re.sub(r"\s+", " ", text)


# ── What a flag actually covers ───────────────────────────────────────────────
# A bare "8-K Item 4.02" made compliance escalate cases whose notice withdrew
# only interim quarters the annual report later superseded (Corpay, Symbotic);
# a bare "4.01" made it escalate routine auditor rotations (SiTime, MasTec).
# The filing says which — so the flag carries one line read from it.

def scope_of_non_reliance(text: str) -> str | None:
    """Which reports an Item 4.02 notice withdraws: interim only, or the annual."""
    i = text.lower().find("item 4.02")
    if i < 0:
        return None
    seg = text[i:i + 1500]
    end = seg.lower().find("relied upon")
    seg = seg[:end] if end > 0 else seg[:700]
    low = seg.lower()
    # "fiscal year ended …" names an annual period; "quarters of fiscal year
    # 2024" (Symbotic) does not, so a bare "fiscal year 2024" is not a signal.
    annual = re.search(r"form 10-k|annual report|fiscal years? ended|audited consolidated", low)
    interim = re.search(r"form 10-q|quarter|interim|unaudited", low)
    if annual:
        return "withdraws annual (audited) financial statements"
    if interim:
        return "withdraws interim (quarterly) statements only — not the annual report"
    return None


def scope_of_auditor_change(text: str) -> str | None:
    """Item 4.01: did the auditor resign or was it replaced, and any disagreements?"""
    i = text.lower().find("item 4.01")
    if i < 0:
        return None
    low = text[i:i + 4000].lower()
    if re.search(r"resign|declin\w* to stand", low):
        how = "auditor RESIGNED"
    elif re.search(r"dismiss|competitive (selection|process|proposal)|request for proposal", low):
        how = "auditor replaced by the company (routine rotation)"
    else:
        how = "new auditor appointed"
    # Only an explicit statement counts. "Disagreement" also appears in the
    # standard clause about NOT consulting the new auditor on any matter that was
    # the subject of a disagreement (Axon, Super Micro) — boilerplate, not a
    # report — so the line never claims disagreements the text doesn't state.
    no_dis = r"\bno\s+(?:\(\w+\)\s*)?[\"“”']?disagreements|were not any[\"“”'\s]+disagreements"
    dis = "no disagreements reported" if re.search(no_dis, low) else "disagreements not stated"
    return f"{how}; {dis}"


_LATE_REASON_WORDS = re.compile(
    r"investigat|audit committee|internal control|material weakness|restat|error|review|"
    r"strategic|transition|additional time|acquisition|cyber", re.I)


def reason_for_late_filing(text: str) -> str | None:
    """The company's own stated reason, quoted from Part III of the NT notice.

    Quoted, not classified: whether a delay is procedural or an accounting
    problem is the judgement compliance has to make, so it gets the sentence.
    """
    # The NARRATIVE heading, not the earlier Part II checkbox text that merely
    # refers to "Part III of this form".
    m = re.search(r"PART\s+III\s*[\u2014\u2013-]+\s*NARRATIVE", text, re.I)
    if not m:
        return None
    i = m.end()
    seg = text[i:i + 3500]
    j = seg.upper().find("PART IV")
    seg = seg[:j] if j > 0 else seg
    k = seg.lower().find("prescribed time period.")       # end of the form's instruction
    if 0 <= k < 400:
        seg = seg[k + len("prescribed time period."):]
    seg = re.sub(r"^\s*\(Attach extra sheets if needed\.?\)\s*", "", seg.strip(), flags=re.I)
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", seg) if len(x.strip()) > 25]
    if not sentences:
        return None
    pick = next((x for x in sentences if _LATE_REASON_WORDS.search(x)), sentences[0])
    if len(pick) <= 300:
        return pick
    # Keep the part of a long sentence that carries the reason.
    k = _LATE_REASON_WORDS.search(pick)
    start = max(0, min(k.start() - 120, len(pick) - 297)) if k else 0
    return ("…" if start else "") + pick[start:start + 297].strip() + "…"


def _late_scope(flag: dict, reason: str | None) -> str | None:
    late = flag.get("late_by")
    parts = []
    if isinstance(late, int):
        parts.append("report filed the same day as this notice" if late == 0
                     else f"report filed {late} days after this notice")
    elif late:
        parts.append("report not yet filed")
    if reason:
        parts.append(f"stated reason: \"{reason}\"")
    return "; ".join(parts) or None


async def _add_scope(flags: list[dict]) -> None:
    """Fill flag["scope"] from the filing text: what a 4.02 withdraws, how an
    auditor left, how late and why a late report was. Never raises."""
    async def one(flag: dict) -> None:
        if flag["label"].startswith("NT "):
            reason = None
            # The quoted reason only for a late ANNUAL report: agents read a
            # digest capped at 2,500 characters per tool, and quoting every late
            # 10-Q pushed Super Micro's auditor resignation past the cap.
            if flag["label"].startswith("NT 10-K") and flag.get("url"):
                try:
                    reason = reason_for_late_filing(await _get_text(flag["url"]))
                except Exception:
                    pass
            flag["scope"] = _late_scope(flag, reason)
            return
        reader = (scope_of_non_reliance if "Item 4.02" in flag["label"] else
                  scope_of_auditor_change if "Item 4.01" in flag["label"] else None)
        if not reader or not flag.get("url"):
            return
        try:
            flag["scope"] = reader(await _get_text(flag["url"]))
        except Exception:
            pass
    await asyncio.gather(*(one(f) for f in flags))


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
        flags: list[dict] | None = None      # None = could not check (no CIK)
        cik = ""

        # Strategy 1: ticker → CIK → submissions API (structured, reliable)
        if ticker:
            cik = await self._ticker_to_cik(ticker)
            if cik:
                filings, flags = await self._filings_from_submissions(cik, max_results)

        # Strategy 2: full-text search by ticker
        if not filings and ticker:
            filings, cik = await self._efts_search(ticker, max_results)

        # Strategy 3: full-text search by company name
        if not filings:
            filings, cik = await self._efts_search(company_clean, max_results)

        # Red flags need the filer's full history, so fetch it even when the
        # filing list came from full-text search.
        if flags is None and cik:
            flags = await self._flags_for_cik(cik)
        if flags:
            await _add_scope(flags)

        return _format_results(company, filings, flags)

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

    async def _flags_for_cik(self, cik: str) -> list[dict] | None:
        try:
            return _red_flags(await _get_json(f"{SUBMISSIONS_BASE}/CIK{cik}.json"), cik)
        except Exception:
            return None

    async def _filings_from_submissions(self, cik: str, max_results: int) -> tuple[list[dict], list[dict] | None]:
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
            return filings, _red_flags(data, cik)
        except Exception:
            return [], None

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
            ciks = (hits[0].get("_source", {}).get("ciks") or [""]) if hits else [""]
            return [_parse_efts_hit(h) for h in hits[:max_results]], str(ciks[0] or "").zfill(10) if ciks[0] else ""
        except Exception:
            return [], ""


# -- Parsers ------------------------------------------------------------------

def _red_flags(submissions: dict, cik: str, today: date | None = None) -> list[dict]:
    """Red-flag filings in the filer's recent history, newest first."""
    recent  = submissions.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    n       = len(forms)
    col     = lambda k: recent.get(k) or [""] * n
    dates, periods, items = col("filingDate"), col("reportDate"), col("items")
    accns, docs = col("accessionNumber"), col("primaryDocument")
    since   = ((today or date.today()) - timedelta(days=FLAG_LOOKBACK_DAYS)).isoformat()
    flags: list[dict] = []
    for i, form in enumerate(forms):
        if (dates[i] or "") < since:
            continue
        labels: list[str] = []
        if form == "8-K":
            codes = [c.strip() for c in str(items[i] or "").split(",")]
            labels = [f"8-K Item {c} — {RED_FLAG_8K_ITEMS[c]}" for c in codes if c in RED_FLAG_8K_ITEMS]
        elif form in RED_FLAG_FORMS:
            labels = [f"{form} — {RED_FLAG_FORMS[form]}"]
        late_by = None
        if form.startswith("NT "):
            # How late was the report itself? Prefer the report for the same
            # period; some notices carry their own filing date as the period
            # (Super Micro), so fall back to the first report filed on or after
            # the notice. Same-day filings can sit on either side in EDGAR's list.
            base = form[3:]
            after = [j for j in range(n) if forms[j] == base and (dates[j] or "") >= dates[i]]
            same = [j for j in after if periods[j] and periods[j] == periods[i]]
            filed = min((dates[j] for j in (same or after)), default=None)
            late_by = ((date.fromisoformat(filed) - date.fromisoformat(dates[i])).days
                       if filed else "not yet filed")
        for label in labels:
            acc = str(accns[i] or "").replace("-", "")
            flags.append({
                "late_by": late_by,
                "filed_at": dates[i], "period": periods[i], "label": label,
                "url": (f"{EDGAR_BASE}/Archives/edgar/data/{int(cik)}/{acc}/{docs[i]}"
                        if acc and docs[i] else ""),
            })
    flags.sort(key=lambda f: f["filed_at"], reverse=True)
    return flags[:MAX_FLAGS]


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

def _format_results(query: str, filings: list[dict], flags: list[dict] | None = None) -> str:
    lines = [f"SEC EDGAR RESULTS for: '{query}'", "=" * 60]

    # Flags FIRST: the risk and compliance agents read a per-tool digest that is
    # truncated, and a red flag at the end of this output would be cut off.
    # (No "[n] FORM —" header or "URL:" line, so the RAG ingester skips it.)
    if flags is not None:
        lines.append(f"REGULATORY FLAGS (last {FLAG_LOOKBACK_DAYS // 365} years of EDGAR filings that "
                     "bear on whether reported figures can be relied on):")
        if flags:
            for f in flags:
                # An 8-K's report date is the EVENT date; for NT and amended
                # reports it is the fiscal period the filing concerns.
                kind = "event" if f["label"].startswith("8-K") else "for period ending"
                period = f" ({kind} {f['period']})" if f.get("period") else ""
                lines.append(f"  ⚑ {f['filed_at']}  {f['label']}{period}")
                if f.get("scope"):
                    lines.append(f"      scope: {f['scope']}")
                if f.get("url"):
                    lines.append(f"      link: {f['url']}")
        else:
            lines.append("  none found.")
        lines.append("")

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
