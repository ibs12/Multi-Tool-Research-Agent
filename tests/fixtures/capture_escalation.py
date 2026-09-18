"""
tests/fixtures/capture_escalation.py
------------------------------------
Empirically pin an *escalating* query and capture its REAL multi-agent run as a
committed fixture. Two purposes:
  1. X1 milestone proof — the compliance-checker genuinely escalates on a real
     query (a company with no citable primary-source financials), producing the
     terminal escalation package (ADR-0009).
  2. Map 2 E4 Tier-1 seed — a deterministic, committed escalated-run snapshot
     the offline scoring gate can assert against with no live LLM.

Live: needs ANTHROPIC_API_KEY + network. Forces the embedded Chroma path so it
matches the zero-external-deps CI path (ADR-0010).

    PGVECTOR_URL= python tests/fixtures/capture_escalation.py "Analyse SpaceX investment outlook"

Writes tests/fixtures/escalation_run.json only when the run actually escalates.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import date

# Run-as-a-script: put the repo root (three levels up) on sys.path.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

os.environ.setdefault("PGVECTOR_URL", "")      # embedded Chroma (zero external deps)
os.environ.setdefault("AGENT_MODE", "multi")

from dotenv import load_dotenv
load_dotenv(".env")

from agent.graph import graph
from agent.state import make_initial_state

FIXTURE = os.path.join(os.path.dirname(__file__), "escalation_run.json")


def _summarize_results(results: list) -> list:
    return [
        {
            "tool_name": r.get("tool_name"),
            "success": r.get("success"),
            "output_preview": (r.get("output", "") or "")[:400],
        }
        for r in results
    ]


async def _run(query: str) -> bool:
    final = await graph.ainvoke(make_initial_state(query, agent_mode="multi"))
    handoff = final.get("handoff", {}) or {}
    escalated = final.get("termination_reason") == "escalated"
    print(f"OUTCOME: termination_reason={final.get('termination_reason')} "
          f"| escalated={escalated} | iterations={final.get('iteration_count')}")
    if not escalated:
        print("Did NOT escalate — not writing fixture. Try another query.")
        return False

    snapshot = {
        "captured_at": date.today().isoformat(),
        "query": query,
        "company_target": final.get("company_target"),
        "agent_mode": "multi",
        "termination_reason": final.get("termination_reason"),
        "iteration_count": final.get("iteration_count"),
        "tools_called": final.get("tools_called"),
        "compliance_verdict": handoff.get("compliance_verdict"),
        "escalation": handoff.get("escalation"),
        "risk_assessment": handoff.get("risk_assessment"),
        "tool_results": _summarize_results(final.get("tool_results", [])),
    }
    with open(FIXTURE, "w") as f:
        json.dump(snapshot, f, indent=2)
    print(f"WROTE fixture: {FIXTURE}")
    return True


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "Analyse SpaceX investment outlook"
    sys.exit(0 if asyncio.run(_run(q)) else 2)
