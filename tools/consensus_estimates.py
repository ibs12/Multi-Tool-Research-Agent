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


def _df_val(df, row_key: str, col) -> float | None:
    """Safely extract a float scalar from a financial DataFrame cell."""
    try:
        if row_key not in df.index:
            return None
        fval = float(df.loc[row_key, col])
        return None if (math.isnan(fval) or math.isinf(fval)) else fval
    except Exception:
        return None


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


# ── Historical financials ─────────────────────────────────────────────────────

def get_historical_financials(ticker: str) -> str:
    """
    Fetch historical annual and quarterly income statement data from Yahoo Finance.

    Uses t.financials / t.income_stmt for annual (last 4 fiscal years) and
    t.quarterly_financials / t.quarterly_income_stmt for quarterly (last 4 quarters).
    Tries both attribute names to handle different yfinance versions gracefully.

    Returns a formatted, cited string ready for injection into agent state.
    Never raises — all failures produce a clear error string.
    """
    if not _HAS_YFINANCE:
        return "[Historical Financials Error] Missing dependency: pip install yfinance"

    ticker = ticker.strip().upper()
    if not ticker:
        return "[Historical Financials Error] Empty ticker symbol provided."

    today_str   = date.today().strftime("%B %d, %Y")
    cite_date   = date.today().strftime("%Y-%m-%d")
    cite        = f"[Source: Yahoo Finance historical financials, retrieved {cite_date}]"

    try:
        t = yf.Ticker(ticker)

        lines = [
            f"HISTORICAL FINANCIALS for: '{ticker}'",
            "=" * 60,
            f"Source: Yahoo Finance | Retrieved: {today_str}",
            "",
        ]

        # ── Annual income statement (last 4 fiscal years) ─────────────────────
        lines.append("ANNUAL (last 4 fiscal years)")
        fin = None
        for attr in ("financials", "income_stmt"):
            try:
                df = getattr(t, attr, None)
                if df is not None and not df.empty:
                    fin = df
                    break
            except Exception:
                continue

        if fin is not None and not fin.empty:
            # sorted() on pd.Timestamp columns gives ascending (oldest first);
            # take the last 4 entries = the 4 most recent fiscal years.
            annual_cols = sorted(fin.columns)[-4:]
            for col in annual_cols:
                fy      = f"FY{col.year}"
                rev     = _safe_rev(_df_val(fin, "Total Revenue", col))
                ni      = _safe_rev(_df_val(fin, "Net Income", col))
                rev_v   = _df_val(fin, "Total Revenue", col)
                gp_v    = _df_val(fin, "Gross Profit", col)
                if rev_v and gp_v and rev_v != 0:
                    gm  = f"{(gp_v / rev_v) * 100:.1f}%"
                else:
                    gm  = "Not available"
                lines.append(
                    f"  {fy}: Revenue {rev} | Net Income {ni} | Gross Margin {gm} {cite}"
                )
        else:
            lines.append("  Not available — no annual income statement data returned.")

        lines.append("")

        # ── Quarterly income statement (last 4 quarters) ──────────────────────
        lines.append("QUARTERLY (last 4 quarters)")
        qfin = None
        for attr in ("quarterly_financials", "quarterly_income_stmt"):
            try:
                df = getattr(t, attr, None)
                if df is not None and not df.empty:
                    qfin = df
                    break
            except Exception:
                continue

        if qfin is not None and not qfin.empty:
            quarterly_cols = sorted(qfin.columns)[-4:]
            for col in quarterly_cols:
                q  = (col.month - 1) // 3 + 1
                ql = f"Q{q} {col.year}"
                rev = _safe_rev(_df_val(qfin, "Total Revenue", col))
                ni  = _safe_rev(_df_val(qfin, "Net Income", col))
                lines.append(f"  {ql}: Revenue {rev} | Net Income {ni} {cite}")
        else:
            lines.append("  Not available — no quarterly income statement data returned.")

        return "\n".join(lines)

    except Exception as e:
        return (
            f"[Historical Financials Error] Could not fetch historical data for '{ticker}': "
            f"{type(e).__name__}: {e}"
        )


def run_historical_financials(ticker: str) -> str:
    """Top-level entry point. Never raises."""
    try:
        return get_historical_financials(ticker)
    except Exception as e:
        return f"[Historical Financials Error] {type(e).__name__}: {e}"


# ── Forward outlook (structured) ──────────────────────────────────────────────
# Everything below produces a JSON-serialisable dict rather than prose: the
# browser charts it directly, so no figure ever passes through the LLM and
# gets to drift. Yahoo exposes per-quarter consensus (0q / +1q) alongside the
# full-year figure (0y), which is what makes a genuine rest-of-year forecast
# possible instead of a naive extrapolation.


def _fy_end_month(t) -> int:
    """Month the fiscal year ends in — 12 for calendar-year filers, 9 for Apple."""
    for attr in ("income_stmt", "financials"):
        try:
            df = getattr(t, attr, None)
            if df is not None and not df.empty:
                return int(sorted(df.columns)[-1].month)
        except Exception:
            continue
    return 12


def _fiscal_period(year: int, month: int, fy_end_month: int) -> tuple[int, int]:
    """(fiscal year, fiscal quarter) for a period ending in the given month."""
    fy = year if month <= fy_end_month else year + 1
    fq = ((month - fy_end_month - 1) % 12) // 3 + 1
    return fy, fq


def _fiscal_label(year: int, month: int, fy_end_month: int) -> str:
    fy, fq = _fiscal_period(year, month, fy_end_month)
    return f"Q{fq} FY{fy}"


def _add_quarters(year: int, month: int, n: int) -> tuple[int, int]:
    """Shift a (year, month) period end forward by n quarters."""
    shifted = month + 3 * n
    return year + (shifted - 1) // 12, (shifted - 1) % 12 + 1


def _f(val) -> float | None:
    """Coerce to a finite float, or None."""
    try:
        if val is None:
            return None
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _i(val) -> int | None:
    f = _f(val)
    return int(f) if f is not None else None


def _actual_quarters(df, row_key: str, fy_end_month: int, limit: int = 6) -> list[dict]:
    """Reported quarters for one income-statement row, oldest first."""
    out: list[dict] = []
    if df is None or getattr(df, "empty", True) or row_key not in df.index:
        return out
    for col in sorted(df.columns):
        val = _df_val(df, row_key, col)
        if val is None:
            continue
        out.append({
            "label":  _fiscal_label(col.year, col.month, fy_end_month),
            "kind":   "actual",
            "value":  val,
            "year":   col.year,
            "month":  col.month,
        })
    return out[-limit:]


def _build_metric(key, label, unit, scale, decimals, actuals, est_df,
                  fy_end_month, year_ago_field) -> dict | None:
    """
    Assemble one metric's outlook: reported quarters, the next two quarters of
    consensus, the full-year consensus, and the arithmetic tying them together.
    """
    if not actuals:
        return None

    last = actuals[-1]
    quarters = [dict(q) for q in actuals]

    # Per-quarter consensus: 0q is the next unreported quarter, +1q the one after.
    for offset, period_key in ((1, "0q"), (2, "+1q")):
        row = _row(est_df, period_key)
        avg = _f(row.get("avg")) if row else None
        if avg is None:
            continue
        y, m = _add_quarters(last["year"], last["month"], offset)
        year_ago = _f(row.get(year_ago_field))
        quarters.append({
            "label":    _fiscal_label(y, m, fy_end_month),
            "kind":     "estimate",
            "value":    avg,
            "low":      _f(row.get("low")),
            "high":     _f(row.get("high")),
            "analysts": _i(row.get("numberOfAnalysts")),
            "year_ago": year_ago,
            "yoy":      _f(row.get("growth")),
            "year":     y,
            "month":    m,
        })

    est_quarters = [q for q in quarters if q["kind"] == "estimate"]
    if not est_quarters:
        return None

    # The fiscal year under forecast is the one the next estimated quarter falls in.
    target_fy, _ = _fiscal_period(est_quarters[0]["year"], est_quarters[0]["month"], fy_end_month)

    fy_row = _row(est_df, "0y")
    fy_avg = _f(fy_row.get("avg")) if fy_row else None
    fy = None
    if fy_avg is not None:
        fy = {
            "label":    f"FY{target_fy}E",
            "value":    fy_avg,
            "low":      _f(fy_row.get("low")),
            "high":     _f(fy_row.get("high")),
            "analysts": _i(fy_row.get("numberOfAnalysts")),
            "yoy":      _f(fy_row.get("growth")),
            "prior":    _f(fy_row.get(year_ago_field)),
        }

    in_fy = lambda q: _fiscal_period(q["year"], q["month"], fy_end_month)[0] == target_fy
    ytd_quarters = [q for q in quarters if q["kind"] == "actual" and in_fy(q)]
    fwd_quarters = [q for q in est_quarters if in_fy(q)]

    ytd_value = sum(q["value"] for q in ytd_quarters) if ytd_quarters else None
    fwd_value = sum(q["value"] for q in fwd_quarters) if fwd_quarters else None

    # Quarters of this fiscal year neither reported nor individually estimated —
    # their total is whatever the full-year consensus leaves over.
    uncovered = max(0, 4 - len(ytd_quarters) - len(fwd_quarters))
    balance = None
    if fy and ytd_value is not None and fwd_value is not None:
        balance = fy["value"] - ytd_value - fwd_value

    # Trailing four reported quarters — the run-rate the consensus is measured against.
    trailing = [q["value"] for q in quarters if q["kind"] == "actual"][-4:]
    run_rate = sum(trailing) if len(trailing) == 4 else None

    for q in quarters:
        q.pop("year", None)
        q.pop("month", None)

    return {
        "key":       key,
        "label":     label,
        "unit":      unit,
        "scale":     scale,
        "decimals":  decimals,
        "quarters":  quarters,
        "fy":        fy,
        "ytd":       {"value": ytd_value, "labels": [q["label"] for q in ytd_quarters]},
        "forecast":  {"value": fwd_value, "labels": [q["label"] for q in fwd_quarters]},
        "uncovered_quarters": uncovered,
        "balance":   balance,
        "run_rate":  run_rate,
    }


def _pct(a: float | None, b: float | None) -> float | None:
    """(a - b) / |b| as a fraction, or None."""
    if a is None or b is None or b == 0:
        return None
    return (a - b) / abs(b)


def _build_drivers(t, info, metrics: list[dict]) -> list[dict]:
    """
    The inputs behind the forecast, stated plainly — coverage depth, the
    year-ago base, dispersion, how consensus sits against the run-rate, and
    how well analysts have predicted this company lately.
    """
    drivers: list[dict] = []
    by_key = {m["key"]: m for m in metrics}
    primary = by_key.get("revenue") or (metrics[0] if metrics else None)

    if primary:
        est = [q for q in primary["quarters"] if q["kind"] == "estimate"]
        counts = [q["analysts"] for q in est if q["analysts"]]
        fy = primary["fy"]
        if counts or (fy and fy.get("analysts")):
            detail = []
            if counts:
                detail.append(f"{max(counts)} per forecast quarter")
            if fy and fy.get("analysts"):
                detail.append(f"{fy['analysts']} on the full year")
            drivers.append({
                "label":  "Analyst coverage",
                "value":  f"{max(counts) if counts else fy['analysts']} analysts",
                "detail": " · ".join(detail),
            })

        nxt = est[0] if est else None
        if nxt and nxt.get("yoy") is not None:
            drivers.append({
                "label":  "Year-ago base",
                "value":  f"{nxt['yoy'] * 100:+.1f}% YoY",
                "detail": f"{nxt['label']}E vs the same quarter a year earlier",
                "tone":   "growth",
            })

        if fy and fy.get("low") is not None and fy.get("high") is not None and fy["value"]:
            spread = (fy["high"] - fy["low"]) / abs(fy["value"]) * 100
            drivers.append({
                "label":  "Estimate dispersion",
                "value":  f"{spread:.1f}% spread",
                "detail": f"{fy['label']} low-to-high range across contributing analysts",
            })

        if fy and primary.get("run_rate"):
            delta = _pct(fy["value"], primary["run_rate"])
            if delta is not None:
                drivers.append({
                    "label":  "Vs trailing run-rate",
                    "value":  f"{delta * 100:+.1f}%",
                    "detail": "Full-year consensus against the last four reported quarters",
                    "tone":   "growth",
                })

        if (fy and primary["ytd"]["value"] is not None
                and primary["forecast"]["value"] is not None):
            path = primary["ytd"]["value"] + primary["forecast"]["value"]
            gap = _pct(path, fy["value"])
            if gap is not None and primary["uncovered_quarters"] == 0:
                drivers.append({
                    "label":  "Quarterly vs annual models",
                    "value":  f"{gap * 100:+.1f}%",
                    "detail": "Reported quarters plus quarterly estimates against the full-year consensus",
                })

    # Recent forecast accuracy — how often analysts have been wrong on this name.
    try:
        hist = t.earnings_history
        if hist is not None and not hist.empty and "surprisePercent" in hist.columns:
            surprises = [_f(v) for v in hist["surprisePercent"].tolist()]
            surprises = [s for s in surprises if s is not None][-4:]
            if surprises:
                beats = sum(1 for s in surprises if s > 0)
                avg = sum(surprises) / len(surprises) * 100
                drivers.append({
                    "label":  "Recent forecast accuracy",
                    "value":  f"beat {beats} of {len(surprises)}",
                    "detail": f"EPS surprise averaged {avg:+.1f}% over the last {len(surprises)} quarters",
                })
    except Exception:
        pass

    rec_key  = info.get("recommendationKey")
    rec_mean = _f(info.get("recommendationMean"))
    if rec_key and rec_key != "none":
        n_op = _i(info.get("numberOfAnalystOpinions"))
        drivers.append({
            "label":  "Analyst recommendation",
            "value":  rec_key.replace("_", " ").title(),
            "detail": (f"Mean score {rec_mean:.2f}/5.0" if rec_mean else "")
                      + (f" across {n_op} opinions" if n_op else ""),
        })

    return drivers


def build_quarterly_outlook(ticker: str) -> dict | None:
    """
    Structured rest-of-year forecast for one ticker.

    Combines reported quarters (Yahoo quarterly income statement / earnings
    history) with per-quarter and full-year analyst consensus, plus the
    factors those estimates rest on. Returns None when a forecast can't be
    assembled. Never raises.
    """
    if not _HAS_YFINANCE:
        return None

    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        if not info.get("quoteType") and not (info.get("longName") or info.get("shortName")):
            return None

        fy_end_month = _fy_end_month(t)

        qfin = None
        for attr in ("quarterly_income_stmt", "quarterly_financials"):
            try:
                df = getattr(t, attr, None)
                if df is not None and not df.empty:
                    qfin = df
                    break
            except Exception:
                continue

        try:
            rev_est = t.revenue_estimate
        except Exception:
            rev_est = None
        try:
            eps_est = t.earnings_estimate
        except Exception:
            eps_est = None

        metrics: list[dict] = []

        rev_actuals = _actual_quarters(qfin, "Total Revenue", fy_end_month)
        rev = _build_metric("revenue", "Revenue", "B", 1e9, 2,
                            rev_actuals, rev_est, fy_end_month, "yearAgoRevenue")
        if rev:
            metrics.append(rev)

        # EPS actuals come from earnings history, not the income statement: analyst
        # estimates are struck on that same adjusted basis, and diluted GAAP EPS
        # is a different number — charting one against the other would be wrong.
        eps_actuals: list[dict] = []
        try:
            hist = t.earnings_history
            if hist is not None and not hist.empty and "epsActual" in hist.columns:
                for idx, val in hist["epsActual"].items():
                    v = _f(val)
                    if v is None:
                        continue
                    eps_actuals.append({
                        "label": _fiscal_label(idx.year, idx.month, fy_end_month),
                        "kind":  "actual",
                        "value": v,
                        "year":  idx.year,
                        "month": idx.month,
                    })
                eps_actuals.sort(key=lambda q: (q["year"], q["month"]))
        except Exception:
            eps_actuals = []

        eps = _build_metric("eps", "EPS (adjusted)", "", 1, 2,
                            eps_actuals, eps_est, fy_end_month, "yearAgoEps")
        if eps:
            metrics.append(eps)

        if not metrics:
            return None

        fy_label = metrics[0]["fy"]["label"] if metrics[0].get("fy") else ""

        return {
            "ticker":   ticker,
            "company":  info.get("longName") or info.get("shortName") or ticker,
            "as_of":    date.today().strftime("%Y-%m-%d"),
            "currency": info.get("financialCurrency") or info.get("currency") or "USD",
            "fy_label": fy_label,
            "metrics":  metrics,
            "drivers":  _build_drivers(t, info, metrics),
            "source":   "Yahoo Finance — reported quarters and analyst consensus",
        }

    except Exception:
        return None
