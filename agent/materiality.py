"""
agent/materiality.py
────────────────────
When are two financial figures *the same number*?

One rule, two consumers. The eval asks it as "did the agent get this right?"
(ADR-era E2); the watchlist asks it as "is this worth telling the user about?"
(ADR-0012). They are the same question, so the tolerances live here — in the
product — and `eval/scoring.py` re-exports them rather than owning them.

Tolerances are per field-type, because a single global percentage is wrong for
all of them: a percentage band is meaningless for EPS quoted in cents, and a
point tolerance is meaningless for a consensus range.

    revenue, net_income   ±0.5% relative (after unit normalisation)
    eps                   ±max($0.01, 0.5%)
    gross_margin          ±0.1 percentage-point absolute
    forward_estimate      inside the consensus [low, high] band
"""

from __future__ import annotations

LEVEL_FIELDS = {"revenue", "net_income"}


def field_match(field: str, expected, actual) -> bool:
    """True when `actual` is the same figure as `expected`, within tolerance.

    None is meaningful, not missing: an `expected` of None means the filing did
    not disclose the figure, so the only correct `actual` is None. An `actual`
    of None against a disclosed figure is an omission, never a match.
    """
    if expected is None:                       # undisclosed → must report nothing
        return actual is None
    if actual is None:                         # omission of a disclosed field
        return False
    if isinstance(expected, dict) and "low" in expected:   # forward estimate band
        try:
            return expected["low"] <= float(actual) <= expected["high"]
        except (TypeError, ValueError, KeyError):
            return False
    try:
        e, a = float(expected), float(actual)
    except (TypeError, ValueError):
        return expected == actual
    if field == "gross_margin":
        return abs(a - e) <= 0.1               # percentage points
    if field == "eps":
        return abs(a - e) <= max(0.01, abs(e) * 0.005)
    if e == 0:
        return a == 0
    return abs(a - e) / abs(e) <= 0.005        # level figures, ±0.5%


def is_material_change(field: str, before, after) -> bool:
    """True when a figure moved enough to be worth surfacing (ADR-0012).

    The inverse of `field_match`, with one deliberate exception: a figure
    appearing or disappearing between runs is NOT a material change. The
    extractor failing to read a cell must never be reported as news — it is
    reported as unknown. Silence from the parser is not evidence of movement.
    """
    if before is None or after is None:
        return False
    return not field_match(field, before, after)


def pct_change(before, after) -> float | None:
    """Relative move, for display. None when it isn't computable."""
    try:
        b, a = float(before), float(after)
    except (TypeError, ValueError):
        return None
    if b == 0:
        return None
    return round((a - b) / abs(b) * 100, 1)
