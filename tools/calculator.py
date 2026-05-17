"""
tools/calculator.py
───────────────────
Two-layer calculator designed for financial research agents:

  Layer 1 — Safe expression evaluator (sympy)
    Handles arithmetic, unit conversions, percentage calculations.
    Completely sandboxed — no eval() on raw strings.

  Layer 2 — Financial ratio library
    Pre-built functions for P/E, EV/EBITDA, debt-to-equity, CAGR, etc.
    The supervisor node can call these by name when it extracts raw
    numbers from SEC filings or news snippets.

Why not just use eval()?
    eval() on LLM-generated expressions is a critical security vulnerability.
    SymPy parses and evaluates expressions in a safe math-only context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

try:
    import sympy
    from sympy import sympify, N
    from sympy.core.sympify import SympifyError
    HAS_SYMPY = True
except ImportError:
    HAS_SYMPY = False


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class CalcResult:
    expression: str
    result: float | str
    formatted: str
    success: bool
    error: str | None = None

    def to_string(self) -> str:
        if not self.success:
            return f"[Calculator Error] {self.error}"
        return f"{self.expression} = {self.formatted}"


# ── Safe expression evaluator ─────────────────────────────────────────────────

# Characters allowed in an expression — blocks any import/exec attempts
_SAFE_PATTERN = re.compile(r'^[\d\s\+\-\*\/\(\)\.\%\^eE,]+$')


def evaluate_expression(expr: str) -> CalcResult:
    """
    Safely evaluate a mathematical expression string.

    Examples:
        "2500 * 1.08 ** 5"          → 3672.61
        "(150 - 120) / 120 * 100"   → 25.0  (% change)
        "1 / 0.035"                 → 28.57 (P/E from earnings yield)
    """
    # Strip "expression:" prefix if the dispatcher passed it through
    expr = expr.strip()
    if expr.lower().startswith("expression:"):
        expr = expr[len("expression:"):].strip()

    if not HAS_SYMPY:
        return CalcResult(expr, "", "", False, "sympy not installed. Run: pip install sympy")

    # Reject anything that looks like code injection
    clean = expr.replace("**", "^")  # allow ^ as power operator
    if not _SAFE_PATTERN.match(clean.replace("^", "").replace("e", "").replace("E", "")):
        return CalcResult(expr, "", "", False, f"Unsafe expression rejected: '{expr}'")

    try:
        # sympify parses the expression; N() evaluates to a float
        parsed = sympify(expr.replace("^", "**"))
        value = float(N(parsed, 6))
        formatted = f"{value:,.4f}".rstrip("0").rstrip(".")
        return CalcResult(expr, value, formatted, True)
    except SympifyError as e:
        return CalcResult(expr, "", "", False, f"Parse error: {e}")
    except Exception as e:
        return CalcResult(expr, "", "", False, str(e))


# ── Financial ratio library ───────────────────────────────────────────────────

def pe_ratio(stock_price: float, eps: float) -> CalcResult:
    """Price-to-Earnings ratio. EPS must be > 0."""
    expr = f"{stock_price} / {eps}"
    if eps <= 0:
        return CalcResult(expr, "", "", False, "EPS must be positive for P/E calculation.")
    val = round(stock_price / eps, 2)
    return CalcResult(expr, val, f"{val:.2f}x", True)


def debt_to_equity(total_debt: float, total_equity: float) -> CalcResult:
    """D/E ratio. Higher = more leveraged (riskier for banks like Citi)."""
    expr = f"{total_debt} / {total_equity}"
    if total_equity == 0:
        return CalcResult(expr, "", "", False, "Total equity is zero — D/E undefined.")
    val = round(total_debt / total_equity, 4)
    return CalcResult(expr, val, f"{val:.2f}x", True)


def cagr(start_value: float, end_value: float, years: float) -> CalcResult:
    """
    Compound Annual Growth Rate.
    Formula: (end / start) ^ (1 / years) - 1
    """
    expr = f"({end_value} / {start_value}) ^ (1 / {years}) - 1"
    if start_value <= 0 or years <= 0:
        return CalcResult(expr, "", "", False, "start_value and years must be > 0.")
    val = round((end_value / start_value) ** (1 / years) - 1, 6)
    return CalcResult(expr, val, f"{val * 100:.2f}%", True)


def ev_to_ebitda(
    market_cap: float,
    total_debt: float,
    cash: float,
    ebitda: float,
) -> CalcResult:
    """
    Enterprise Value / EBITDA.
    EV = Market Cap + Total Debt - Cash
    """
    ev = market_cap + total_debt - cash
    expr = f"EV({ev:,.0f}) / EBITDA({ebitda:,.0f})"
    if ebitda <= 0:
        return CalcResult(expr, "", "", False, "EBITDA must be positive.")
    val = round(ev / ebitda, 2)
    return CalcResult(expr, val, f"{val:.2f}x", True)


def revenue_growth(current: float, previous: float) -> CalcResult:
    """Year-over-year revenue growth rate."""
    expr = f"({current} - {previous}) / {previous} * 100"
    if previous == 0:
        return CalcResult(expr, "", "", False, "Previous revenue is zero.")
    val = round((current - previous) / previous * 100, 2)
    return CalcResult(expr, val, f"{val:+.2f}%", True)


def profit_margin(net_income: float, revenue: float) -> CalcResult:
    """Net profit margin as a percentage."""
    expr = f"{net_income} / {revenue} * 100"
    if revenue == 0:
        return CalcResult(expr, "", "", False, "Revenue is zero.")
    val = round(net_income / revenue * 100, 2)
    return CalcResult(expr, val, f"{val:.2f}%", True)


# ── Dispatcher — called by the LangGraph node ─────────────────────────────────

# Maps names the supervisor can request → (function, required arg names)
RATIO_REGISTRY: dict[str, Any] = {
    "pe_ratio": pe_ratio,
    "debt_to_equity": debt_to_equity,
    "cagr": cagr,
    "ev_to_ebitda": ev_to_ebitda,
    "revenue_growth": revenue_growth,
    "profit_margin": profit_margin,
    "expression": evaluate_expression,   # raw math fallback
}


def run_calculator(query: str) -> str:
    """
    Entry point for the LangGraph calculator node.

    `query` can be:
      - A raw math expression:  "150 / 12.5"
      - A ratio request:        "pe_ratio: stock_price=182.5 eps=6.13"
      - A natural language hint the supervisor formats before calling

    Returns a plain string suitable for inclusion in the agent state.
    """
    query = query.strip()

    # ── Try ratio dispatcher first ─────────────────────────────────────────
    for name, fn in RATIO_REGISTRY.items():
        if name == "expression":
            continue
        if query.lower().startswith(name):
            try:
                # Parse "key=value" pairs after the ratio name
                params_str = query[len(name):].strip().lstrip(":")
                params = _parse_kwargs(params_str)
                result = fn(**params)
                return f"CALCULATOR RESULT\n{result.to_string()}"
            except Exception as e:
                return f"[Calculator Error] Failed to call {name}: {e}"

    # ── Fall back to safe expression evaluator ────────────────────────────
    result = evaluate_expression(query)
    return f"CALCULATOR RESULT\n{result.to_string()}"


def _parse_kwargs(params_str: str) -> dict[str, float]:
    """
    Parse 'stock_price=182.5 eps=6.13' → {'stock_price': 182.5, 'eps': 6.13}
    """
    pairs = re.findall(r'(\w+)\s*=\s*([\d\.\-]+)', params_str)
    return {k: float(v) for k, v in pairs}