"""
agent/deltas.py
───────────────
What changed about a company between two Runs (ADR-0012).

Compares *structured figures* — not brief prose, which the model rewords every
run so thoroughly that a company where nothing happened would produce an
enormous diff. Materiality is decided by the same per-field tolerances the eval
uses (`agent/materiality.py`), so presentation rounding never surfaces as news.

Three rules that keep the output trustworthy:
  1. A figure is compared only where BOTH runs read it. A field the parser
     missed is *unknown*, never a change.
  2. A change is reported only if it clears the field's tolerance.
  3. An empty delta is a real, expected answer — most days, nothing moved.
"""

from __future__ import annotations

from agent.materiality import is_material_change, pct_change


def _figures(run: dict) -> dict:
    return (run or {}).get("figures") or {}


def _verdict(run: dict) -> str | None:
    return ((run or {}).get("compliance_verdict") or {}).get("verdict")


def _red_flags(run: dict) -> list:
    return ((run or {}).get("risk_assessment") or {}).get("red_flags") or []


# ── What a figure MEANS can change between runs ──────────────────────────────
# Reported-year EPS was adjusted (non-GAAP) EPS, taken from earnings history,
# until the historical-financials tool started supplying GAAP diluted EPS
# (#57, live 2026-09-27 23:52 UTC). Comparing across that line reports a
# definition change as news: AMD FY2025 "fell" from $4.17 (adjusted) to $2.65
# (diluted) on the first refresh after the fix, with nothing new about AMD.
#
# So every run carries the basis its figures were read on, and a figure is only
# compared between runs that share it. Runs saved before the stamp existed get
# the basis that was live when they were created.
EPS_REPORTED_BASIS = "gaap-diluted"
_GAAP_EPS_SINCE = "2026-09-27T23:52:00+00:00"


def figures_basis(run: dict) -> dict:
    """The definitions a run's figures were read on (see EPS_REPORTED_BASIS)."""
    run = run or {}
    if run.get("figures_basis"):
        return run["figures_basis"]
    created = str(run.get("created_at") or "")
    return {"eps_reported": EPS_REPORTED_BASIS if created >= _GAAP_EPS_SINCE else "adjusted"}


def _basis_key(field: str, period: str) -> str | None:
    """Which basis governs this cell. Estimate columns (FY2026E…) come from
    analyst consensus on both sides of the change, so they stay comparable."""
    if field == "eps" and not period.endswith("E"):
        return "eps_reported"
    return None


def compute_delta(before: dict, after: dict) -> dict:
    """Material differences between two stored Runs of the same company.

    `before` may be None/empty (the first ever Run for a company), in which case
    there is nothing to compare and the delta is empty — a first Run is a
    baseline, not news.
    """
    figure_changes, not_compared = [], []
    before_figs, after_figs = _figures(before), _figures(after)
    basis_before, basis_after = figures_basis(before), figures_basis(after)

    for period, fields in sorted(after_figs.items()):
        prior = before_figs.get(period) or {}
        for field, new_value in sorted(fields.items()):
            old_value = prior.get(field)
            if old_value is None or new_value is None:
                continue                      # unknown, not a change (rule 1)
            key = _basis_key(field, period)
            if key and basis_before.get(key) != basis_after.get(key):
                # Different definitions: unknown, not a change (rule 1) — but
                # say so, so a missing comparison is visible, not silent.
                not_compared.append({
                    "period": period, "field": field,
                    "reason": f"definition changed ({basis_before.get(key)} → {basis_after.get(key)})",
                })
                continue
            if not is_material_change(field, old_value, new_value):
                continue                      # inside tolerance (rule 2)
            figure_changes.append({
                "period": period,
                "field": field,
                "before": old_value,
                "after": new_value,
                "pct_change": pct_change(old_value, new_value),
            })

    verdict_change = None
    vb, va = _verdict(before), _verdict(after)
    if vb and va and vb != va:
        verdict_change = {"before": vb, "after": va}

    flag_change = None
    fb, fa = len(_red_flags(before)), len(_red_flags(after))
    if before and fb != fa:
        flag_change = {"before": fb, "after": fa}

    return {
        "figure_changes": figure_changes,
        "verdict_change": verdict_change,
        "red_flag_change": flag_change,
        "not_compared": not_compared,
        "is_empty": not (figure_changes or verdict_change or flag_change),
    }


def _fmt(field: str, value: float) -> str:
    if field == "gross_margin":
        return f"{value:.1f}%"
    if field == "eps":
        return f"${value:,.2f}"
    if abs(value) >= 1e9:
        return f"${value / 1e9:,.2f}B"
    if abs(value) >= 1e6:
        return f"${value / 1e6:,.1f}M"
    return f"${value:,.0f}"


def summarise_delta(delta: dict, company: str = "") -> str:
    """One line fit for a notification — the pointer, not the detail (ADR-0011)."""
    if not delta or delta.get("is_empty"):
        return f"{company}: no material change".strip(": ")
    parts = []
    for change in delta.get("figure_changes", [])[:3]:
        pct = change.get("pct_change")
        move = f" ({pct:+.1f}%)" if pct is not None else ""
        parts.append(f"{change['field'].replace('_', ' ')} {change['period']} "
                     f"{_fmt(change['field'], change['before'])} → "
                     f"{_fmt(change['field'], change['after'])}{move}")
    if delta.get("verdict_change"):
        v = delta["verdict_change"]
        parts.append(f"verdict {v['before']} → {v['after']}")
    if delta.get("red_flag_change"):
        f = delta["red_flag_change"]
        parts.append(f"risk flags {f['before']} → {f['after']}")
    return f"{company}: " + " · ".join(parts) if company else " · ".join(parts)
