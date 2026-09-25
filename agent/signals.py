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
  - **price** — a move beyond a threshold *since the last Refresh*. Not over a
    trailing window: a window keeps re-reporting a move the agent has already
    researched, so one +10% day would buy a fresh Refresh on every sweep until
    it scrolled out of the window.

Neither raises: a signal source that is down must degrade to "no signal", never
break the sweep.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from agent.sec_client import recent_filings

MATERIAL_FORMS = ("10-K", "10-Q", "8-K")
DEFAULT_PRICE_THRESHOLD_PCT = 7.0

_MARKET_TZ = ZoneInfo("America/New_York")
_MARKET_CLOSE = time(16, 0)


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


def _daily_closes(ticker: str, start: str) -> list[tuple[datetime, float]]:
    """(market-close time, close) per trading day from `start`, oldest first."""
    import yfinance
    hist = yfinance.Ticker(ticker).history(start=start, interval="1d")
    out = []
    for day, close in zip(hist.index, hist["Close"].tolist()):
        if close != close:                                   # drop NaN
            continue
        out.append((datetime.combine(day.date(), _MARKET_CLOSE, _MARKET_TZ), float(close)))
    return out


def price_signal(ticker: str, since: str | None,
                 threshold_pct: float = DEFAULT_PRICE_THRESHOLD_PCT) -> dict | None:
    """A move beyond `threshold_pct` since the last Refresh at `since`, or None.

    The reference is the last close the previous Refresh could have seen — a
    run at 11:00 ET saw yesterday's close, not today's. With no previous
    Refresh there is nothing to compare against, same as a filing baseline.
    """
    if not ticker or not since:
        return None
    try:
        ran_at = datetime.fromisoformat(since)
        # a week of slack so the reference exists across weekends and holidays
        start = (ran_at.astimezone(_MARKET_TZ).date() - timedelta(days=7)).isoformat()
        bars = _daily_closes(ticker, start)
    except Exception:
        return None
    seen = [c for closed_at, c in bars if closed_at <= ran_at]
    if not seen or seen[-1] == 0 or bars[-1][0] <= ran_at:
        return None                  # no reference, or no close since the Refresh
    reference, latest = seen[-1], bars[-1][1]
    move = (latest - reference) / abs(reference) * 100
    if abs(move) < threshold_pct:
        return None
    return {
        "kind": "price",
        "move_pct": round(move, 1),
        "reference_close": round(reference, 2),
        "detail": f"price {move:+.1f}% since last refresh",
    }


def detect(company: dict, last_refresh_at: str | None = None) -> dict | None:
    """The first signal that fires for a watchlist row, or None.

    `last_refresh_at` is the `created_at` of the company's latest Run — the
    point the price move is measured from.

    Filing first: a filing is hard evidence that the numbers changed, whereas a
    price move only says the market reacted to something — possibly to news the
    agent cannot verify anyway.
    """
    signal = None
    if company.get("cik"):
        signal = filing_signal(company["cik"], company.get("last_seen_accession"))
    if not signal and company.get("ticker"):
        signal = price_signal(company["ticker"], last_refresh_at)
    return signal
