"""
tests/test_eval_scoring.py
--------------------------
The Map 2 scoring logic (E2/E3) — pure, deterministic unit tests. Part of the
Tier-1 CI gate: it guards the metric definitions themselves against regression.
"""

from __future__ import annotations

from eval.scoring import (classify_escalation, field_match, score_case_extraction,
                          score_dataset)


# ── E2.Q1 match rules ─────────────────────────────────────────────────────────

def test_level_field_half_percent_tolerance():
    assert field_match("revenue", 391_035_000_000, 391_000_000_000)     # within 0.5%
    assert not field_match("revenue", 391_035_000_000, 380_000_000_000)  # ~2.8% off


def test_eps_cent_or_half_percent():
    assert field_match("eps", 6.13, 6.14)          # 1 cent
    assert not field_match("eps", 6.13, 6.30)      # 17 cents, >0.5%


def test_gross_margin_absolute_point():
    assert field_match("gross_margin", 46.2, 46.15)     # 0.05 pp
    assert not field_match("gross_margin", 46.2, 46.5)  # 0.3 pp


def test_forward_estimate_band():
    band = {"low": 468, "high": 485}
    assert field_match("forward_estimate", band, 478)
    assert not field_match("forward_estimate", band, 500)


def test_undisclosed_field_must_not_be_reported():
    assert field_match("gross_margin", None, None)       # correctly says nothing
    assert not field_match("gross_margin", None, 42.0)   # fabricated a number


def test_omission_of_a_disclosed_field_fails():
    assert not field_match("net_income", 1000, None)


# ── E2 extraction + fabrication ───────────────────────────────────────────────

def test_fabricating_an_undisclosed_margin_is_counted():
    case = {"tested_fields": {
        "net_income": {"expected_value": 1000, "source_accession": "A"},
        "gross_margin": {"expected_value": None, "source_accession": "A"}}}
    got = score_case_extraction(case, {
        "net_income": {"value": 1000, "cited_accession": "A"},
        "gross_margin": {"value": 42.0}})
    assert got["per_field"]["net_income"] == "correct"
    assert got["fabrications"] == 1


# ── E3 classification ─────────────────────────────────────────────────────────

def test_escalation_confusion_cells():
    assert classify_escalation("escalate", "escalated") == "true_escalate"
    assert classify_escalation("escalate", "brief") == "false_clear"
    assert classify_escalation("clear", "escalated") == "false_escalate"
    assert classify_escalation("clear", "brief") == "true_clear"
    assert classify_escalation("escalate", "no_decision") == "no_decision"


def test_score_dataset_rates_and_pairing():
    cases = [
        {"id": "e1", "category": "should_escalate", "expected_verdict": "escalate",
         "gap_type": "unverifiable_citation", "tested_fields": {"x": {"expected_value": 1, "source_accession": "A"}}},
        {"id": "e2", "category": "should_escalate", "expected_verdict": "escalate",
         "gap_type": "unverifiable_citation", "is_boundary": True,
         "tested_fields": {"x": {"expected_value": 1, "source_accession": "A"}}},
        {"id": "c1", "category": "correct_extraction", "expected_verdict": "clear",
         "tested_fields": {"revenue": {"expected_value": 100, "source_accession": "A"}}},
    ]
    runs = {
        "e1": {"terminal": "escalated", "fields": {}},           # true escalate
        "e2": {"terminal": "brief", "fields": {}},               # boundary false-clear
        "c1": {"terminal": "brief",
               "fields": {"revenue": {"value": 100, "cited_accession": "A"}}},
    }
    m = score_dataset(cases, runs)
    assert m["false_clear_rate"] == 0.5           # 1 of 2 should-escalate cleared
    assert m["false_escalate_rate"] == 0.0        # the clear case wasn't escalated
    assert m["boundary_false_clear_rate"] == 1.0  # the one boundary case false-cleared
    assert m["field_accuracy"] == 1.0             # revenue matched
    assert m["citation_correctness"] == 1.0
