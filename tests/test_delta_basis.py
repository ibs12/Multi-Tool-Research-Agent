"""
tests/test_delta_basis.py
-------------------------
A delta must not report a change of definition as news (ADR-0012). Found live:
AMD's first refresh after #57 showed FY2025 EPS "falling" 36.5%, $4.17 → $2.65 —
adjusted EPS in the older run, GAAP diluted EPS in the newer one.
"""

from __future__ import annotations

from agent.deltas import EPS_REPORTED_BASIS, compute_delta, figures_basis
from api.run_store import build_run_record

BEFORE_FIX = "2026-09-23T22:28:41+00:00"     # AMD's baseline
AFTER_FIX = "2026-09-28T22:36:00+00:00"      # AMD's first refresh after #57


def _run(created_at, figures, **extra):
    return {"created_at": created_at, "figures": figures, **extra}


def test_amd_eps_definition_change_is_not_reported_as_news():
    before = _run(BEFORE_FIX, {"FY2025": {"eps": 4.17}, "FY2026E": {"eps": 7.58},
                               "Q2 FY2026": {"gross_margin": 56.0}})
    after = _run(AFTER_FIX, {"FY2025": {"eps": 2.65}, "FY2026E": {"eps": 7.58},
                             "Q2 FY2026": {"gross_margin": 53.8}})
    d = compute_delta(before, after)
    changed = {(c["period"], c["field"]) for c in d["figure_changes"]}
    assert ("FY2025", "eps") not in changed                       # definition change, not news
    assert ("Q2 FY2026", "gross_margin") in changed               # a real change still surfaces
    assert d["not_compared"] == [{"period": "FY2025", "field": "eps",
                                  "reason": "definition changed (adjusted → gaap-diluted)"}]


def test_estimate_eps_stays_comparable_across_the_change():
    """FY2026E comes from analyst consensus on both sides of the change."""
    before = _run(BEFORE_FIX, {"FY2026E": {"eps": 7.58}})
    after = _run(AFTER_FIX, {"FY2026E": {"eps": 9.10}})
    d = compute_delta(before, after)
    assert [(c["period"], c["field"]) for c in d["figure_changes"]] == [("FY2026E", "eps")]
    assert d["not_compared"] == []


def test_same_basis_compares_reported_eps_normally():
    before = _run(AFTER_FIX, {"FY2025": {"eps": 2.65}})
    after = _run("2026-12-01T00:00:00+00:00", {"FY2025": {"eps": 3.10}},
                 figures_basis={"eps_reported": EPS_REPORTED_BASIS})
    d = compute_delta(before, after)
    assert [(c["period"], c["field"]) for c in d["figure_changes"]] == [("FY2025", "eps")]


def test_only_a_definition_change_leaves_the_delta_empty():
    before = _run(BEFORE_FIX, {"FY2025": {"eps": 4.17, "revenue": 25.8e9}})
    after = _run(AFTER_FIX, {"FY2025": {"eps": 2.65, "revenue": 25.8e9}})
    d = compute_delta(before, after)
    assert d["is_empty"] and len(d["not_compared"]) == 1


def test_legacy_runs_get_the_basis_that_was_live_when_they_were_made():
    assert figures_basis({"created_at": BEFORE_FIX}) == {"eps_reported": "adjusted"}
    assert figures_basis({"created_at": AFTER_FIX}) == {"eps_reported": EPS_REPORTED_BASIS}


def test_new_runs_are_stamped_with_their_basis():
    rec = build_run_record("Analyse AMD", "multi", {"company_target": "AMD"}, "", 1.0)
    assert rec["figures_basis"] == {"eps_reported": EPS_REPORTED_BASIS}
