"""
eval/run_full.py
────────────────
The out-of-band full before/after run (E5 headline). Each multi-agent case takes
minutes (research loop + risk + compliance + possible hand-back), so the full
set × two arms is a multi-hour batch job — run it on a server / nightly CI, not
interactively. See eval/RUNBOOK.md.

Properties for a long job:
- **Resumable**: every case's result is appended to a durable per-arm checkpoint
  (eval/runs/<arm>.checkpoint.jsonl) the moment it finishes. A restart skips
  everything already done — kill and resume freely.
- **Robust**: a single case that raises is recorded as no_decision+error and the
  run continues; one bad case never sinks the batch.
- **Composes the LIVE eval set**: the frozen dataset's correct/ambiguous/missing
  cases (real companies the agent can research) PLUS the curated live escalation
  QUERIES. The dataset's synthetic should_escalate cases are excluded — they
  perturb a label citation for *static* scoring and never trigger a live run.

    PGVECTOR_URL= python eval/run_full.py --arm single    # ~hours
    PGVECTOR_URL= python eval/run_full.py --arm multi     # ~many hours
    PGVECTOR_URL= python eval/run_full.py --arm both      # sequential
    python eval/run_full.py --report-only                 # score+compare from checkpoints
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PGVECTOR_URL", "")

from eval import tracking
from eval.compare import build_report, _render_md
from eval.run_eval import extract_fields
from eval.schema import read_jsonl
from eval.scoring import score_dataset

_HERE = Path(__file__).resolve().parent
DATASET = _HERE / "dataset" / "cases.jsonl"
LIVE_ESCALATION = _HERE / "live_escalation_cases.jsonl"
RUNS_DIR = _HERE / "runs"


def load_live_eval_set() -> list[dict]:
    """dataset correct/ambiguous/missing + the live escalation queries."""
    dataset = read_jsonl(DATASET)
    live = [c for c in dataset if c["category"] != "should_escalate"]
    if LIVE_ESCALATION.exists():
        live += read_jsonl(LIVE_ESCALATION)
    return live


def _load_checkpoint(arm: str) -> dict:
    ckpt = RUNS_DIR / f"{arm}.checkpoint.jsonl"
    done = {}
    if ckpt.exists():
        for line in ckpt.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["case_id"]] = rec["result"]
    return done


def _append_checkpoint(arm: str, case_id: str, result: dict) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with (RUNS_DIR / f"{arm}.checkpoint.jsonl").open("a") as f:
        f.write(json.dumps({"case_id": case_id, "result": result}) + "\n")


async def _run_case(case: dict, arm: str) -> dict:
    from agent.graph import graph
    from agent.nodes.synthesis import synthesis_node
    from agent.state import make_initial_state

    query = f"Analyse {case['company']} {case['period']} financial results"
    final = await graph.ainvoke(make_initial_state(query, agent_mode=arm))
    if final.get("termination_reason") == "escalated":
        return {"terminal": "escalated", "fields": {}}
    brief = synthesis_node(final).get("final_report", "")
    return {"terminal": "brief" if brief.strip() else "no_decision",
            "fields": extract_fields(brief, case["period"])}


async def run_arm_resumable(cases: list[dict], arm: str) -> dict:
    done = _load_checkpoint(arm)
    todo = [c for c in cases if c["id"] not in done]
    print(f"[{arm}] {len(done)} done, {len(todo)} to run", flush=True)
    for i, case in enumerate(todo, 1):
        try:
            result = await _run_case(case, arm)
        except Exception as e:
            result = {"terminal": "no_decision", "fields": {}, "error": str(e)}
        _append_checkpoint(arm, case["id"], result)
        done[case["id"]] = result
        print(f"[{arm}] {i}/{len(todo)} {case['id']} -> {result['terminal']}", flush=True)
    return done


def _score_and_record(cases: list[dict]) -> dict | None:
    single, multi = _load_checkpoint("single"), _load_checkpoint("multi")
    ids = {c["id"] for c in cases}
    if not (ids <= set(single) and ids <= set(multi)):
        print(f"Not both arms complete — single {len(ids & set(single))}/{len(ids)}, "
              f"multi {len(ids & set(multi))}/{len(ids)}. Run the remaining arm(s).",
              flush=True)
        return None
    version = "unknown"
    manifest = DATASET.parent / "manifest.json"
    if manifest.exists():
        version = json.loads(manifest.read_text()).get("dataset_version", "unknown")
    result = {
        "single": {"metrics": score_dataset(cases, single), "seconds": None},
        "multi": {"metrics": score_dataset(cases, multi), "seconds": None},
    }
    for arm in ("single", "multi"):
        tracking.record_run(arm, result[arm]["metrics"],
                            {"dataset_version": version, "n_cases": len(cases),
                             "run": "full", "model": os.getenv("CLAUDE_MODEL", "claude-opus-4-8")})
    report = build_report(cases, result, version)
    (_HERE / "full_comparison_report.json").write_text(json.dumps(report, indent=2))
    (_HERE / "full_comparison_report.md").write_text(_render_md(report))
    print(_render_md(report))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["single", "multi", "both"], default="both")
    ap.add_argument("--limit", type=int, default=0, help="cap cases (smoke)")
    ap.add_argument("--report-only", action="store_true",
                    help="score+compare from existing checkpoints, run nothing")
    args = ap.parse_args()

    cases = load_live_eval_set()
    if args.limit:
        cases = cases[:args.limit]
    print(f"live eval set: {len(cases)} cases "
          f"({sum(c['expected_verdict']=='escalate' for c in cases)} should-escalate)",
          flush=True)

    if not args.report_only:
        arms = ["single", "multi"] if args.arm == "both" else [args.arm]
        for arm in arms:
            asyncio.run(run_arm_resumable(cases, arm))

    _score_and_record(cases)


if __name__ == "__main__":
    main()
