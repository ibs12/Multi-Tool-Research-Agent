"""
eval/run_eval.py
────────────────
Tier-2 harness (E4.Q3/Q4): run one arm (single- or multi-agent) over the frozen
eval dataset, score it (E2/E3), and record the aggregate run to the tracker,
pinning dataset/model/prompt versions so the E5 before/after is apples-to-apples.

This is the *expensive* path — a live LLM call per case — so it is on-demand /
nightly, NOT the merge gate (that is the deterministic Tier-1 gate in
tests/test_eval_gate.py). E5 drives this over both arms on the identical
substrate (AGENT_MODE toggled) and diffs the two recorded runs.

    PGVECTOR_URL= python eval/run_eval.py --arm multi  --limit 20
    PGVECTOR_URL= python eval/run_eval.py --arm single --limit 20

Field extraction from the brief is best-effort (parses the mandated Financial
Snapshot table, E2.Q3); the LLM-judge fallback for prose-only figures and the
citation appears-in-retrieved-chunks check are E2 execution, layered later.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PGVECTOR_URL", "")

from eval import tracking
from eval.schema import read_jsonl
from eval.scoring import score_dataset

# The snapshot-table parser is a product concern (the watchlist reads the same
# figures to compute deltas, ADR-0012), so it lives in agent/brief_parser.py and
# is re-exported here under the name this harness has always used.
from agent.brief_parser import extract_period as extract_fields  # noqa: E402,F401


async def run_arm(cases: list[dict], agent_mode: str) -> dict:
    from agent.graph import graph
    from agent.nodes.synthesis import synthesis_node
    from agent.state import make_initial_state

    runs = {}
    for case in cases:
        query = f"Analyse {case['company']} {case['period']} financial results"
        final = await graph.ainvoke(make_initial_state(query, agent_mode=agent_mode))
        if final.get("termination_reason") == "escalated":
            result = {"terminal": "escalated", "fields": {}}
        else:
            brief = synthesis_node(final).get("final_report", "")
            result = {"terminal": "brief" if brief.strip() else "no_decision",
                      "fields": extract_fields(brief, case["period"])}
        runs[case["id"]] = result
        tracking.record_case(agent_mode, case["id"],
                             {"terminal": result["terminal"], "period": case["period"]})
    return runs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["single", "multi"], required=True)
    ap.add_argument("--dataset", default=str(Path(__file__).resolve().parent / "dataset" / "cases.jsonl"))
    ap.add_argument("--limit", type=int, default=0, help="cap cases (smoke runs)")
    args = ap.parse_args()

    cases = read_jsonl(args.dataset)
    if args.limit:
        cases = cases[:args.limit]

    runs = asyncio.run(run_arm(cases, args.arm))
    metrics = score_dataset(cases, runs)

    manifest_path = Path(args.dataset).parent / "manifest.json"
    version = "unknown"
    if manifest_path.exists():
        import json
        version = json.loads(manifest_path.read_text()).get("dataset_version", "unknown")
    params = {
        "dataset_version": version,
        "model": os.getenv("CLAUDE_MODEL", "claude-opus-4-8"),
        "n_cases": len(cases),
    }
    where = tracking.record_run(args.arm, metrics, params)

    import json
    print(json.dumps({"arm": args.arm, "recorded": where, "metrics": metrics}, indent=2))


if __name__ == "__main__":
    main()
