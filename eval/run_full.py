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

    # One arm, one slice — e.g. extraction accuracy of the production pipeline,
    # the number ADR-0012's deltas rest on, without paying for both arms:
    PGVECTOR_URL= python eval/run_full.py --arm multi --category correct_extraction --since 2022

`--since` matters for extraction: the research tools fetch a company's *latest*
filings and the brief's snapshot covers FY2022 onward, so an FY2019 case finds
no source data and compliance (correctly) escalates — it measures a scope
limit, not extraction accuracy.
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
BOUNDARY = _HERE / "boundary_cases.jsonl"
RUNS_DIR = _HERE / "runs"


def load_live_eval_set() -> list[dict]:
    """dataset correct/ambiguous/missing + the live escalation queries + the
    hand-curated boundary slice (real filers, so they run live like any case)."""
    dataset = read_jsonl(DATASET)
    live = [c for c in dataset if c["category"] != "should_escalate"]
    if LIVE_ESCALATION.exists():
        live += read_jsonl(LIVE_ESCALATION)
    if BOUNDARY.exists():
        live += read_jsonl(BOUNDARY)
    return live


def _fiscal_year(case: dict) -> int | None:
    import re
    m = re.search(r"(\d{4})", str(case.get("period", "")))
    return int(m.group(1)) if m else None


def case_fingerprint(case: dict) -> str:
    """What a checkpointed answer was an answer TO: the query it ran (company +
    period) and the key it will be scored against. If the dataset is
    regenerated and a case's key changes — the #58 fiscal-year relabel moved
    Walmart's and NVIDIA's figures between periods — the old answer must be
    re-run, not silently scored against the new key."""
    import hashlib
    blob = json.dumps([case["company"], case["period"], case["tested_fields"]], sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def _model() -> str:
    return os.getenv("CLAUDE_MODEL", "claude-opus-4-8")


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
    # Why, not just what: an escalation or a bad figure is only interpretable
    # next to the compliance reason and the tools that failed to deliver.
    handoff = final.get("handoff") or {}
    why = {
        "failed_tools": sorted({r.get("tool_name") for r in final.get("tool_results") or []
                                if not r.get("success")} - {None}),
        "compliance": (handoff.get("compliance_verdict") or {}).get("verdict"),
    }
    if final.get("termination_reason") == "escalated":
        pkg = handoff.get("escalation") or {}
        return {"terminal": "escalated", "fields": {}, **why,
                "escalation_reason": str(pkg.get("reason") or "")[:600],
                "gap_type": (handoff.get("compliance_verdict") or {}).get("gap_type")}
    brief = synthesis_node(final).get("final_report", "")
    return {"terminal": "brief" if brief.strip() else "no_decision",
            "fields": extract_fields(brief, case["period"]), **why}


async def run_arm_resumable(cases: list[dict], arm: str) -> dict:
    done = _load_checkpoint(arm)
    # A checkpoint from another model is not a head start, it is contamination:
    # resuming would silently score one arm on two models.
    other = {r.get("model") for r in done.values() if r.get("model") not in (None, _model())}
    if other:
        raise SystemExit(f"[{arm}] checkpoint has results from {sorted(other)}, but CLAUDE_MODEL="
                         f"{_model()}. Move eval/runs/{arm}.checkpoint.jsonl aside to start fresh.")
    # Re-run a finished case only if what it answered has changed. Lines written
    # before fingerprints existed are kept: their ids still carry the period
    # text the query used.
    stale = [c for c in cases if c["id"] in done
             and done[c["id"]].get("case_fp") not in (None, case_fingerprint(c))]
    for c in stale:
        del done[c["id"]]
    todo = [c for c in cases if c["id"] not in done]
    print(f"[{arm}] {len(done)} done, {len(todo)} to run"
          + (f" ({len(stale)} re-run: answer key changed)" if stale else ""), flush=True)
    for i, case in enumerate(todo, 1):
        try:
            result = await _run_case(case, arm)
        except Exception as e:
            result = {"terminal": "no_decision", "fields": {}, "error": str(e)}
        result["model"] = _model()
        result["dataset_version"] = _dataset_version()
        result["case_fp"] = case_fingerprint(case)
        _append_checkpoint(arm, case["id"], result)
        done[case["id"]] = result
        print(f"[{arm}] {i}/{len(todo)} {case['id']} -> {result['terminal']}", flush=True)
    return done


def _dataset_version() -> str:
    manifest = DATASET.parent / "manifest.json"
    if manifest.exists():
        return json.loads(manifest.read_text()).get("dataset_version", "unknown")
    return "unknown"


def score_single_arm(cases: list[dict], arm: str, label: str) -> dict | None:
    """Score one arm on its own — no comparison, just the arm's own numbers.

    Written to eval/runs/<arm>-<label>.report.{json,md}. Only cases this arm has
    finished are scored, and the report says how many that is, so a partial run
    can't pass for a complete one.
    """
    runs = _load_checkpoint(arm)
    finished = [c for c in cases if c["id"] in runs]
    if not finished:
        print(f"[{arm}] nothing finished yet — no report.", flush=True)
        return None
    m = score_dataset(finished, runs)
    errors = sum(1 for c in finished if runs[c["id"]].get("error"))
    report = {"arm": arm, "slice": label, "model": _model(), "dataset_version": _dataset_version(),
              "cases_finished": len(finished), "cases_in_slice": len(cases),
              "cases_errored": errors, "metrics": m}
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    (RUNS_DIR / f"{arm}-{label}.report.json").write_text(json.dumps(report, indent=2))
    md = _render_single_md(report)
    (RUNS_DIR / f"{arm}-{label}.report.md").write_text(md)
    print(md, flush=True)
    tracking.record_run(arm, m, {"dataset_version": report["dataset_version"],
                                 "n_cases": len(finished), "run": f"single-arm:{label}",
                                 "model": report["model"]})
    return report


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _render_single_md(r: dict) -> str:
    m = r["metrics"]
    lines = [
        f"# Eval — {r['arm']} arm, `{r['slice']}`",
        "",
        f"Model `{r['model']}` · dataset `{r['dataset_version']}` · "
        f"**{r['cases_finished']}/{r['cases_in_slice']} cases finished** "
        f"({r['cases_errored']} errored, scored as no decision)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Field accuracy | {_pct(m['field_accuracy'])} |",
        f"| Citation correctness | {_pct(m['citation_correctness'])} |",
        f"| Fabricated figures | {m['fabrication_count']} |",
        f"| Omitted figures | {m['omission_count']} |",
        f"| False-escalate rate | {_pct(m['false_escalate_rate'])} |",
        f"| False-clear rate | {_pct(m['false_clear_rate'])} |",
        f"| **Boundary false-clear rate** | {_pct(m['boundary_false_clear_rate'])} |",
        f"| No decision | {m['no_decision_count']} |",
        "",
        "| Field | Accuracy |",
        "|---|---|",
    ]
    lines += [f"| {f} | {_pct(v)} |" for f, v in sorted(m["field_accuracy_by_field"].items())]
    return "\n".join(lines) + "\n"


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
    ap.add_argument("--category", action="append", default=[],
                    help="only these case categories (repeatable), e.g. correct_extraction")
    ap.add_argument("--boundary-only", action="store_true",
                    help="only the hand-curated boundary slice (the headline safety metric)")
    ap.add_argument("--since", type=int, default=0,
                    help="only cases whose fiscal year is >= this (e.g. 2022, the brief's range)")
    ap.add_argument("--report-only", action="store_true",
                    help="score+compare from existing checkpoints, run nothing")
    args = ap.parse_args()

    cases = load_live_eval_set()
    if args.boundary_only:
        cases = [c for c in cases if c.get("is_boundary")]
    if args.category:
        cases = [c for c in cases if c["category"] in set(args.category)]
    if args.since:
        cases = [c for c in cases if (_fiscal_year(c) or 0) >= args.since]
    if args.limit:
        cases = cases[:args.limit]
    print(f"live eval set: {len(cases)} cases "
          f"({sum(c['expected_verdict']=='escalate' for c in cases)} should-escalate)",
          flush=True)

    if not args.report_only:
        arms = ["single", "multi"] if args.arm == "both" else [args.arm]
        for arm in arms:
            asyncio.run(run_arm_resumable(cases, arm))

    # A slice or a single arm can't be a before/after comparison — score it alone.
    if args.category or args.boundary_only or args.arm != "both":
        label = ("boundary" if args.boundary_only else "+".join(sorted(args.category)) or "all") \
            + (f"-fy{args.since}+" if args.since else "")
        for arm in (["single", "multi"] if args.arm == "both" else [args.arm]):
            score_single_arm(cases, arm, label)
        return
    _score_and_record(cases)


if __name__ == "__main__":
    main()
