"""
tests/test_full_run.py
----------------------
Guards the out-of-band full-run harness (eval/run_full.py) — deterministic, no
LLM: the live-eval-set composition (E6 feedback) and checkpoint resumability.
"""

from __future__ import annotations

import os

import pytest

from eval import run_full

_HAS_DATA = os.path.exists(run_full.DATASET)
pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="eval dataset not generated")


def test_live_eval_set_excludes_synthetic_and_includes_live_escalation():
    cases = run_full.load_live_eval_set()
    cats = {c["category"] for c in cases}
    # No dataset should_escalate (synthetic label-perturbation) cases in the LIVE set...
    dataset_synth = [c for c in cases
                     if c["category"] == "should_escalate" and c.get("synthetic")]
    assert not dataset_synth
    # ...but the curated live escalation QUERIES (now fictional companies) are present.
    live = [c for c in cases if str(c.get("source", "")).startswith("live-escalation-query")]
    assert len(live) >= 1
    assert all(c["expected_verdict"] == "escalate" for c in live)


def test_checkpoint_resume_skips_completed(tmp_path, monkeypatch):
    monkeypatch.setattr(run_full, "RUNS_DIR", tmp_path)
    cases = run_full.load_live_eval_set()
    assert run_full._load_checkpoint("multi") == {}          # nothing yet
    run_full._append_checkpoint("multi", cases[0]["id"], {"terminal": "escalated"})
    done = run_full._load_checkpoint("multi")
    assert cases[0]["id"] in done
    todo = [c for c in cases if c["id"] not in done]
    assert len(todo) == len(cases) - 1                       # exactly one skipped
