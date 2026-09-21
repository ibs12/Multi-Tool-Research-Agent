"""
agent/signals.py
────────────────
Cheap, deterministic, LLM-free detection that a tracked company may have changed
(ADR-0011). Signals decide *whether* to spend a Refresh; they are never the
answer themselves.

Polling is free, so it happens continuously. Refreshing is minutes and real
money, so it happens rarely — the asymmetry is the whole design:

    ~10 companies × ~8 filings/year  ≈  $28/year
    ~10 companies × nightly          ≈  $105/month, mostly re-researching
                                        companies where nothing happened

Two signals:
  - **filing** — a new 10-K/10-Q/8-K for the company's CIK. Identity is the
    accession number, not a date: dates make "is this new?" ambiguous for
    same-day filings, and a date comparison that says "yes" forever would
    refresh on every sweep.
  - **price** — a move beyond a threshold over a short window.

Neither raises: a signal source that is down must degrade to "no signal", never
break the sweep.
"""

from __future__ import annotations

from agent.sec_client import recent_filings

MATERIAL_FORMS = ("10-K", "10-Q", "8-K")
DEFAULT_PRICE_THRESHOLD_PCT = 7.0
DEFAULT_PRICE_WINDOW_DAYS = 5


def filing_signal(cik: int | str, last_seen_accession: str | None,
                  forms: tuple[str, ...] = MATERIAL_FORMS) -> dict | None:
    """A qualifying filing the watchlist hasn't seen yet, or None.

    `last_seen_accession` is the newest accession recorded at the previous
    sweep. When it is None the company has no baseline yet, so the caller
    decides what that means (the worker treats "never run" as a baseline
    Refresh) — this function only reports novelty it can prove.
    """
    try:
        filings = recent_filings(cik, forms=forms, limit=5)
    except Exception:
        return None                      # source down ⇒ no signal, never a crash
    if not filings:
        return None
    newest = filings[0]
    if last_seen_accession and newest["accession"] == last_seen_accession:
        return None
    if not last_seen_accession:
        return None                      # nothing to compare against yet
    return {
        "kind": "filing",
        "form": newest["form"],
        "accession": newest["accession"],
        "filed_at": newest["filed_at"],
        "detail": f"{newest['form']} filed {newest['filed_at']}",
    }


def newest_accession(cik: int | str,
                     forms: tuple[str, ...] = MATERIAL_FORMS) -> str | None:
    """The current newest qualifying accession — the baseline a sweep records."""
    try:
        filings = recent_filings(cik, forms=forms, limit=1)
    except Exception:
        return None
    return filings[0]["accession"] if filings else None


def price_signal(ticker: str,
                 threshold_pct: float = DEFAULT_PRICE_THRESHOLD_PCT,
                 window_days: int = DEFAULT_PRICE_WINDOW_DAYS) -> dict | None:
    """A price move beyond `threshold_pct` over the window, or None."""
    if not ticker:
        return None
    try:
        import yfinance
        hist = yfinance.Ticker(ticker).history(period=f"{max(window_days, 2)}d")
        closes = [float(c) for c in hist["Close"].tolist() if c == c]  # drop NaN
    except Exception:
        return None
    if len(closes) < 2 or closes[0] == 0:
        return None
    move = (closes[-1] - closes[0]) / abs(closes[0]) * 100
    if abs(move) < threshold_pct:
        return None
    return {
        "kind": "price",
        "move_pct": round(move, 1),
        "window_days": window_days,
        "detail": f"price {move:+.1f}% over {window_days}d",
    }


def detect(company: dict) -> dict | None:
    """The first signal that fires for a watchlist row, or None.

    Filing first: a filing is hard evidence that the numbers changed, whereas a
    price move only says the market reacted to something — possibly to news the
    agent cannot verify anyway.
    """
    signal = None
    if company.get("cik"):
        signal = filing_signal(company["cik"], company.get("last_seen_accession"))
    if not signal and company.get("ticker"):
        signal = price_signal(company["ticker"])
    return signal
