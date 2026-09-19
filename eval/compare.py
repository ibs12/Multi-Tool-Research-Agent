"""
eval/compare.py
───────────────
E5 — the single-agent-vs-multi-agent before/after, run on the identical
substrate (same dataset, model, MCP RAG, shared prompts; only AGENT_MODE
differs — E5.Q1/Q3). Produces the **capability/quality claim split** (E5.Q2),
evaluated against a **pre-registered decision rule** (E5.Q5).

The split is the honest core (E5.Q2): the single-agent baseline has NO
escalation mechanism, so a paired false-clear delta against it would hit
significance by construction and measure nothing about judgement. So:

  • Capability claim (contextual, NOT a delta): the prior architecture cannot
    escalate at all; the new one can. Reported as an added capability.
  • Quality claim (headline): the multi-agent arm's boundary-only false-clear
    and false-escalate as ABSOLUTE numbers, on their own merits.
  • Genuine deltas (both arms equally capable): field accuracy, citation
    correctness, fabrication — single vs multi.

    PGVECTOR_URL= python eval/compare.py --dataset eval/demo_cases.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PGVECTOR_URL", "")

from eval import tracking
from eval.run_eval import run_arm
from eval.schema import read_jsonl
from eval.scoring import score_dataset

# Pre-registered BEFORE running (E5.Q5): "multi-agent wins" is defined against an
# ABSOLUTE bar, not a false-clear delta the baseline structurally couldn't move.
DECISION_RULE = {
    "boundary_false_clear_rate_max": 0.15,   # catch the marginal should-escalate cases
    "false_escalate_rate_max": 0.10,         # without over-escalating the clear ones
    "field_accuracy_regression_max": 0.02,   # multi must not regress extraction
}


def _escalations(metrics: dict) -> int:
    c = metrics.get("confusion", {})
    return c.get("true_escalate", 0) + c.get("false_escalate", 0)


def evaluate_decision(single: dict, multi: dict) -> dict:
    """Apply the pre-registered rule; honest about null (a bar with no data)."""
    checks, null = {}, False
    bfc = multi.get("boundary_false_clear_rate")
    checks["boundary_false_clear"] = (
        None if bfc is None else bfc <= DECISION_RULE["boundary_false_clear_rate_max"])
    fer = multi.get("false_escalate_rate")
    checks["false_escalate"] = (
        None if fer is None else fer <= DECISION_RULE["false_escalate_rate_max"])
    ma, sa = multi.get("field_accuracy"), single.get("field_accuracy")
    checks["no_field_regression"] = (
        None if (ma is None or sa is None)
        else ma >= sa - DECISION_RULE["field_accuracy_regression_max"])

    null = any(v is None for v in checks.values())
    verdict = ("null (insufficient data on this subset)" if null
               else "multi-agent wins" if all(checks.values())
               else "mixed / does not clear the bar")
    return {"checks": checks, "verdict": verdict}


async def _run_both(cases: list[dict]) -> dict:
    out = {}
    for arm in ("single", "multi"):
        start = time.time()
        runs = await run_arm(cases, arm)
        elapsed = round(time.time() - start, 1)
        metrics = score_dataset(cases, runs)
        out[arm] = {"metrics": metrics, "runs": runs, "seconds": elapsed}
    return out


def build_report(cases: list[dict], result: dict, version: str) -> dict:
    single, multi = result["single"]["metrics"], result["multi"]["metrics"]
    n_escalatable = sum(1 for c in cases if c["expected_verdict"] == "escalate")
    return {
        "dataset_version": version,
        "n_cases": len(cases),
        "n_should_escalate": n_escalatable,
        # (1) capability — contextual, NOT a delta
        "capability_claim": {
            "single_agent_escalations": _escalations(single),
            "multi_agent_escalations": _escalations(multi),
            "statement": ("The single-agent baseline has no escalation mechanism "
                          f"({_escalations(single)} escalations); the multi-agent "
                          f"pipeline escalated {_escalations(multi)}. This is an "
                          "added capability, not a percentage-point improvement."),
        },
        # (2) quality — headline, multi ABSOLUTE numbers
        "quality_claim": {
            "false_clear_rate": multi.get("false_clear_rate"),
            "boundary_false_clear_rate": multi.get("boundary_false_clear_rate"),
            "false_escalate_rate": multi.get("false_escalate_rate"),
            "false_clear_by_gap_type": multi.get("false_clear_by_gap_type"),
        },
        # (3) genuine deltas — both arms equally capable
        "extraction_deltas": {
            "field_accuracy": {"single": single.get("field_accuracy"),
                               "multi": multi.get("field_accuracy")},
            "citation_correctness": {"single": single.get("citation_correctness"),
                                     "multi": multi.get("citation_correctness")},
            "fabrication_count": {"single": single.get("fabrication_count"),
                                  "multi": multi.get("fabrication_count")},
        },
        "decision": evaluate_decision(single, multi),
        "cost_latency": {
            "single_seconds": result["single"]["seconds"],
            "multi_seconds": result["multi"]["seconds"],
            "note": "wall-clock; token cost is a follow-up (needs per-call usage capture).",
        },
    }


def _render_md(report: dict) -> str:
    q, d = report["quality_claim"], report["decision"]
    ex = report["extraction_deltas"]
    lines = [
        f"# Before/after — single vs multi ({report['n_cases']} cases, "
        f"{report['n_should_escalate']} should-escalate)",
        f"_dataset: {report['dataset_version']}_", "",
        "## 1. Capability (contextual, not a delta)",
        report["capability_claim"]["statement"], "",
        "## 2. Quality — multi-agent, absolute (headline)",
        f"- boundary false-clear: **{q['boundary_false_clear_rate']}**",
        f"- overall false-clear: {q['false_clear_rate']}  |  "
        f"false-escalate (guardrail): **{q['false_escalate_rate']}**", "",
        "## 3. Genuine deltas (equal-capability metrics)",
        f"- field accuracy: single {ex['field_accuracy']['single']} → "
        f"multi {ex['field_accuracy']['multi']}",
        f"- citation correctness: single {ex['citation_correctness']['single']} → "
        f"multi {ex['citation_correctness']['multi']}",
        f"- fabrications: single {ex['fabrication_count']['single']} → "
        f"multi {ex['fabrication_count']['multi']}", "",
        f"## Decision (pre-registered): **{d['verdict']}**",
        f"checks: {json.dumps(d['checks'])}", "",
        f"_latency: single {report['cost_latency']['single_seconds']}s, "
        f"multi {report['cost_latency']['multi_seconds']}s_",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(Path(__file__).resolve().parent / "demo_cases.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cases = read_jsonl(args.dataset)
    if args.limit:
        cases = cases[:args.limit]

    manifest = Path(args.dataset).parent / "dataset" / "manifest.json"
    version = (json.loads(manifest.read_text()).get("dataset_version", "demo")
               if manifest.exists() else Path(args.dataset).stem)

    result = asyncio.run(_run_both(cases))
    report = build_report(cases, result, version)

    for arm in ("single", "multi"):
        tracking.record_run(arm, result[arm]["metrics"],
                            {"dataset_version": version, "n_cases": len(cases),
                             "model": os.getenv("CLAUDE_MODEL", "claude-opus-4-8")})

    out_dir = Path(args.dataset).parent
    (out_dir / "comparison_report.json").write_text(json.dumps(report, indent=2))
    (out_dir / "comparison_report.md").write_text(_render_md(report))
    print(_render_md(report))


if __name__ == "__main__":
    main()
