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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PGVECTOR_URL", "")

from eval import tracking
from eval.schema import read_jsonl
from eval.scoring import score_dataset

_ROW_TO_FIELD = {
    "revenue": "revenue", "net income": "net_income",
    "eps": "eps", "gross margin": "gross_margin",
}
_NUM = re.compile(r"\$?\s*(-?[\d,]+(?:\.\d+)?)\s*(billion|million|b|m|%)?", re.I)


def _to_number(text: str, field: str):
    m = _NUM.search(text)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit in ("billion", "b"):
        val *= 1e9
    elif unit in ("million", "m"):
        val *= 1e6
    return val


def _cells(line: str) -> list[str]:
    """Split a markdown table row, dropping the empty cells the outer pipes
    produce so the label lands at index 0 and columns align with the header."""
    parts = [c.strip() for c in line.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


_EMPTY_CELL = {"", "—", "-", "–", "n/a", "N/A"}


def extract_fields(brief: str, period: str) -> dict:
    """Best-effort parse of the mandated Financial Snapshot table (E2.Q3): the
    cell at (row=field, column=period) for each tested row. Returns
    {field: {value, cited_accession}}. cited_accession stays None — the brief
    cites source *types* ([SEC Filing]), not accessions; verifying a figure
    against the retrieved chunks is the E2.Q4 step, layered later. When the table
    yields nothing, an LLM-judge fallback for prose-only figures is the
    documented E2.Q3 extension (not wired here)."""
    table_lines = [ln for ln in brief.splitlines() if ln.count("|") >= 2]
    want = period.replace("FY", "")
    header = col = None
    for ln in table_lines:
        cells = _cells(ln)
        for i, c in enumerate(cells):
            if want in c and ("FY" in c or "20" in c):   # a year column, not a separator
                header, col = cells, i
                break
        if header:
            break
    if header is None:
        return {}
    fields = {}
    for ln in table_lines:
        cells = _cells(ln)
        if cells == header or col >= len(cells) or not cells:
            continue
        label = cells[0].lower()
        cell = cells[col]
        if cell in _EMPTY_CELL:
            continue
        for key, field in _ROW_TO_FIELD.items():
            if key in label:
                num = _to_number(cell, field)
                if num is not None:
                    fields[field] = {"value": num, "cited_accession": None}
    return fields


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
