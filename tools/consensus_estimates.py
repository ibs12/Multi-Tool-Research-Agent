"""
tools/consensus_estimates.py
------------------------------
Fetches forward-looking analyst consensus estimates from Yahoo Finance
via the yfinance library.

Every estimate is annotated with the analyst count and retrieval date so
the synthesis layer can produce properly attributed, traceable citations.

Output citation format per data point:
    [Analyst Consensus — {n} analysts, Yahoo Finance, retrieved {date}]

Interview talking point:
    "I built this to go beyond trailing financials. SEC filings give us
     the last 10-K; consensus estimates give us where 40+ analysts think
     the company is headed. Pairing historic SEC data with forward
     estimates in one brief is how buy-side analysts actually work."
"""

from __future__ import annotations

import math
from datetime import date

try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    _HAS_YFINANCE = False


# ── Formatting helpers ────────────────────────────────────────────────────────

def _safe_eps(val) -> str:
    """Format an EPS value; return 'Not available' for None or NaN."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "Not available"
    return f"${float(val):.2f}"


def _safe_rev(val) -> str:
    """Format a revenue value in billions; return 'Not available' for None/NaN."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "Not available"
    return f"${float(val) / 1e9:.2f}B"


def _safe_score(val) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "Not available"
    return f"{float(val):.2f}"


def _n(val) -> str:
    """Format analyst count; return 'N/A' for missing values."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "N/A"
    return str(int(val))


def _citation(n_analysts) -> str:
    today_str = date.today().strftime("%Y-%m-%d")
    return f"[Analyst Consensus — {_n(n_analysts)} analysts, Yahoo Finance, retrieved {today_str}]"


def _current_quarter() -> str:
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"Q{q} {today.year}"


def _next_quarter() -> str:
    today = date.today()
    q = (today.month - 1) // 3 + 1
    return f"Q1 {today.year + 1}" if q == 4 else f"Q{q + 1} {today.year}"


# ── Row accessor ──────────────────────────────────────────────────────────────

def _row(df, key: str) -> dict:
    """Return a DataFrame row as a plain dict; empty dict if key not found."""
    try:
        if df is not None and not df.empty and key in df.index:
            return df.loc[key].to_dict()
    except Exception:
        pass
    return {}


# ── Main entry point ──────────────────────────────────────────────────────────

def run_consensus_estimates(ticker: str) -> str:
    """
    Fetch analyst consensus estimates from Yahoo Finance.
    Returns a formatted string ready for injection into agent state.
    Never raises — all exceptions produce a clear error message.
    Never fabricates data: missing values are reported as 'Not available'.
    """
    if not _HAS_YFINANCE:
        return "[Consensus Estimates Error] Missing dependency: pip install yfinance"

    ticker = ticker.strip().upper()
    if not ticker:
        return "[Consensus Estimates Error] Empty ticker symbol provided."

    today_str = date.today().strftime("%B %d, %Y")

    try:
        t    = yf.Ticker(ticker)
        info = t.info or {}

        company_name = info.get("longName") or info.get("shortName") or ""
        # Invalid tickers return a nearly empty info dict (no quoteType, no name)
        if not info.get("quoteType") and not company_name:
            return (
                f"[Consensus Estimates Error] Ticker '{ticker}' not found on Yahoo Finance. "
                f"Ensure the symbol is correct (e.g. 'AAPL', 'JPM', 'GOOGL')."
            )

        company_display = company_name or ticker
        n_total         = info.get("numberOfAnalystOpinions")

        lines = [
            f"ANALYST CONSENSUS ESTIMATES for: '{company_display} ({ticker})'",
            "=" * 60,
            f"Source: Yahoo Finance | Analysts surveyed: {_n(n_total)} | Retrieved: {today_str}",
            "",
        ]

        # ── EPS Estimates ─────────────────────────────────────────────────────
        lines.append("EPS ESTIMATES")
        try:
            ee = t.earnings_estimate
            if ee is not None and not ee.empty:
                periods = [
                    ("0q",  f"Current Quarter ({_current_quarter()})"),
                    ("+1q", f"Next Quarter ({_next_quarter()})"),
                    ("0y",  f"Current Year ({date.today().year})"),
                    ("+1y", f"Next Year ({date.today().year + 1})"),
                ]
                for key, label in periods:
                    r = _row(ee, key)
                    if r:
                        n    = r.get("numberOfAnalysts")
                        cite = _citation(n)
                        lines.append(
                            f"  {label}: {_safe_eps(r.get('avg'))} "
                            f"(Low: {_safe_eps(r.get('low'))} | High: {_safe_eps(r.get('high'))}) "
                            f"{cite}"
                        )
                    else:
                        lines.append(f"  {label}: Not available")
            else:
                lines.append("  Not available — no EPS estimate data returned.")
        except Exception as exc:
            lines.append(f"  Not available ({type(exc).__name__}: {exc})")

        lines.append("")

        # ── Revenue Estimates ─────────────────────────────────────────────────
        lines.append("REVENUE ESTIMATES")
        try:
            re_df = t.revenue_estimate
            if re_df is not None and not re_df.empty:
                rev_periods = [
                    ("0y",  f"Current Year ({date.today().year})"),
                    ("+1y", f"Next Year ({date.today().year + 1})"),
                ]
                for key, label in rev_periods:
                    r = _row(re_df, key)
                    if r:
                        n    = r.get("numberOfAnalysts")
                        cite = _citation(n)
                        lines.append(
                            f"  {label}: {_safe_rev(r.get('avg'))} "
                            f"(Low: {_safe_rev(r.get('low'))} | High: {_safe_rev(r.get('high'))}) "
                            f"{cite}"
                        )
                    else:
                        lines.append(f"  {label}: Not available")
            else:
                lines.append("  Not available — no revenue estimate data returned.")
        except Exception as exc:
            lines.append(f"  Not available ({type(exc).__name__}: {exc})")

        lines.append("")

        # ── Analyst Recommendation ────────────────────────────────────────────
        lines.append("ANALYST RECOMMENDATION")
        rec_key  = info.get("recommendationKey") or "n/a"
        rec_mean = info.get("recommendationMean")
        rec_disp = rec_key.replace("_", " ").title() if rec_key != "n/a" else "Not available"
        lines.append(
            f"  Consensus: {rec_disp} | Mean score: {_safe_score(rec_mean)}/5.0"
        )

        try:
            rs = t.recommendations_summary
            if rs is not None and not rs.empty:
                latest      = rs.iloc[0].to_dict()
                strong_buy  = _n(latest.get("strongBuy"))
                buy         = _n(latest.get("buy"))
                hold        = _n(latest.get("hold"))
                sell        = _n(latest.get("sell"))
                strong_sell = _n(latest.get("strongSell"))
                lines.append(
                    f"  Strong Buy: {strong_buy} | Buy: {buy} | Hold: {hold} "
                    f"| Sell: {sell} | Strong Sell: {strong_sell}"
                )
            else:
                lines.append("  Breakdown by rating: Not available")
        except Exception:
            lines.append("  Breakdown by rating: Not available")

        return "\n".join(lines)

    except Exception as e:
        return (
            f"[Consensus Estimates Error] Could not fetch estimates for '{ticker}': "
            f"{type(e).__name__}: {e}"
        )
