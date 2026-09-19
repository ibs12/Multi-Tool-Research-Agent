"""
tests/test_eval_gate.py
-----------------------
The Tier-1 CI eval-gate (E4.Q3): deterministic, no live LLM. It exercises the
tracking adapter's pure threshold check and scores the committed escalation
fixture (the merged E4 seed) to prove the metric plumbing catches a real
should-escalate outcome. This is the merge-blocking shape; the expensive live
run (Tier-2, eval/run_eval.py) is on-demand.
"""

from __future__ import annotations

import json
import os

import pytest

from eval import tracking
from eval.scoring import score_dataset

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "escalation_run.json")


# ── assert_thresholds (pure gate core) ────────────────────────────────────────

def test_thresholds_pass_when_metrics_meet_bounds():
    tracking.assert_thresholds(
        {"false_clear_rate": 0.0, "field_accuracy": 0.9},
        {"false_clear_rate": ("<=", 0.1), "field_accuracy": (">=", 0.8)},
    )  # no raise


def test_thresholds_raise_on_violation():
    with pytest.raises(tracking.ThresholdError) as e:
        tracking.assert_thresholds({"false_clear_rate": 0.3},
                                   {"false_clear_rate": ("<=", 0.1)})
    assert "false_clear_rate" in str(e.value)


def test_thresholds_raise_on_missing_metric():
    # A None metric (no should-escalate cases ran) must NOT silently pass.
    with pytest.raises(tracking.ThresholdError):
        tracking.assert_thresholds({"false_clear_rate": None},
                                   {"false_clear_rate": ("<=", 0.1)})


# ── record_run local fallback (mlflow-optional) ───────────────────────────────

def test_record_run_local_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(tracking, "_mlflow", lambda: None)
    monkeypatch.setattr(tracking, "_RUNS_DIR", tmp_path)
    where = tracking.record_run("multi", {"false_clear_rate": 0.0}, {"dataset_version": "t"})
    assert where["backend"] == "local"
    assert json.loads((tmp_path / "multi.run.json").read_text())["metrics"]["false_clear_rate"] == 0.0


# ── the escalation fixture, scored end-to-end (E4.Q3 seed) ─────────────────────

@pytest.mark.skipif(not os.path.exists(_FIX), reason="escalation fixture missing")
def test_escalation_fixture_scores_as_a_caught_escalation():
    fx = json.load(open(_FIX))
    # Treat the recorded escalated run as a should-escalate case; the scorer must
    # classify it a true-escalate (false_clear_rate == 0) and the gate must pass.
    case = {"id": "fx", "category": "should_escalate", "expected_verdict": "escalate",
            "gap_type": fx["compliance_verdict"].get("gap_type") or "unverifiable_citation",
            "tested_fields": {"net_income": {"expected_value": 1, "source_accession": "A"}}}
    runs = {"fx": {"terminal": "escalated", "fields": {}}}
    metrics = score_dataset([case], runs)
    assert metrics["false_clear_rate"] == 0.0
    tracking.assert_thresholds(metrics, {"false_clear_rate": ("<=", 0.0)})
