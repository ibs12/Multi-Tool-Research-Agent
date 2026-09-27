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


def test_category_slice_is_the_extraction_set():
    cases = [c for c in run_full.load_live_eval_set() if c["category"] == "correct_extraction"]
    assert len(cases) == 68
    assert all(c["expected_verdict"] == "clear" for c in cases)


def test_single_arm_report_scores_only_finished_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(run_full, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(run_full.tracking, "record_run", lambda *a, **k: None)
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    cases = [c for c in run_full.load_live_eval_set() if c["category"] == "correct_extraction"][:3]
    run_full._append_checkpoint("multi", cases[0]["id"],
                                {"terminal": "brief", "fields": {}, "model": "claude-sonnet-4-6"})
    report = run_full.score_single_arm(cases, "multi", "correct_extraction")
    assert report["cases_finished"] == 1 and report["cases_in_slice"] == 3
    md = (tmp_path / "multi-correct_extraction.report.md").read_text()
    assert "1/3 cases finished" in md


def test_resume_refuses_a_checkpoint_from_another_model(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(run_full, "RUNS_DIR", tmp_path)
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    cases = run_full.load_live_eval_set()[:2]
    run_full._append_checkpoint("multi", cases[0]["id"],
                                {"terminal": "brief", "fields": {}, "model": "claude-opus-4-8"})
    with pytest.raises(SystemExit, match="claude-opus-4-8"):
        asyncio.run(run_full.run_arm_resumable(cases, "multi"))


def test_since_keeps_only_in_range_fiscal_years():
    cases = [c for c in run_full.load_live_eval_set()
             if c["category"] == "correct_extraction" and (run_full._fiscal_year(c) or 0) >= 2022]
    assert len(cases) == 35
    assert all(run_full._fiscal_year(c) >= 2022 for c in cases)


def test_a_changed_answer_key_forces_a_rerun(tmp_path, monkeypatch):
    """#58: a regenerated dataset may move figures between periods; an answer
    checkpointed against the old key must be re-run, not re-scored."""
    import asyncio
    monkeypatch.setattr(run_full, "RUNS_DIR", tmp_path)
    monkeypatch.setenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    cases = run_full.load_live_eval_set()[:3]
    old = dict(cases[0], tested_fields={"revenue": {"expected_value": 1, "source_accession": "x"}})
    run_full._append_checkpoint("multi", cases[0]["id"], {"terminal": "brief", "fields": {},
        "model": "claude-sonnet-4-6", "case_fp": run_full.case_fingerprint(old)})
    run_full._append_checkpoint("multi", cases[1]["id"], {"terminal": "brief", "fields": {},
        "model": "claude-sonnet-4-6", "case_fp": run_full.case_fingerprint(cases[1])})
    run_full._append_checkpoint("multi", cases[2]["id"], {"terminal": "brief", "fields": {},
        "model": "claude-sonnet-4-6"})                     # legacy line, no fingerprint
    ran = []

    async def fake(case, arm):
        ran.append(case["id"])
        return {"terminal": "brief", "fields": {}}
    monkeypatch.setattr(run_full, "_run_case", fake)
    asyncio.run(run_full.run_arm_resumable(cases, "multi"))
    assert ran == [cases[0]["id"]]                         # only the changed key re-runs


def test_fiscal_year_label_follows_the_filer():
    from eval.generate_dataset import fiscal_year_label as fy
    assert fy({"end": "2025-01-31"}) == 2025      # Walmart / NVIDIA: FY named by end year
    assert fy({"end": "2024-06-30"}) == 2024      # Microsoft
    assert fy({"end": "2023-12-31"}) == 2023
    assert fy({"end": "2022-01-02"}) == 2021      # J&J 52/53-week year closing in early Jan
    assert fy({"end": "2023-01-01"}) == 2022
