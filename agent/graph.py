"""
agent/graph.py
--------------
Assembles the LangGraph StateGraph for the research loop.

Topology:
    [START] → supervisor → dispatcher → supervisor (loops)
                        ↘                          ↘
                         [END]                      [END]

Synthesis is NOT a node in this graph — it is called separately by both
run.py (CLI) and api/main.py (API) after the graph completes.

Parallel tool execution:
    async_tool_dispatcher is a proper async def so LangGraph's astream()
    awaits it directly in the running event loop.  Sync tool nodes
    (wikipedia, calculator, arxiv) are wrapped with asyncio.to_thread so
    they don't block the loop.  Async tool nodes (web_search, sec_edgar,
    rag_search) are awaited directly.  All tools run concurrently via
    asyncio.gather — no ThreadPoolExecutor needed.

    Under graph.invoke() (CLI), LangGraph runs async nodes via asyncio.run()
    internally so the same code works in both sync and async contexts.
"""

from __future__ import annotations

import asyncio
import inspect
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.nodes.supervisor import supervisor_node
from agent.nodes.tools import (
    web_search_node,
    wikipedia_node,
    arxiv_node,
    sec_edgar_node,
    rag_search_node,
    consensus_estimates_node,
)

load_dotenv()

TOOL_REGISTRY = {
    "web_search":           web_search_node,
    "wikipedia":            wikipedia_node,
    "arxiv":                arxiv_node,
    "sec_edgar":            sec_edgar_node,
    "rag_search":           rag_search_node,
    "consensus_estimates":  consensus_estimates_node,
}


# ── Tool ordering (structural, not prompted) ──────────────────────────────────
# A depends on B  ⟺  A consumes B's dispatcher-written output from shared state.
# Membership rule keeps this complete: the next dependent tool is a one-line add.
# (ADR-0002. Argument/state preconditions are a separate concern — see GUARDS.)
PREREQUISITES: dict[str, list[str]] = {
    "rag_search": ["sec_edgar"],   # rag ingests the filing URLs sec_edgar writes
}


def _latest_success(state: AgentState) -> dict[str, bool]:
    """Map each tool_name to whether its most recent result succeeded."""
    status: dict[str, bool] = {}
    for r in state.get("tool_results", []):
        status[r["tool_name"]] = bool(r.get("success"))
    return status


def _dispatcher_error(base: str, unmet: list[str]) -> dict:
    msg = (f"[Dispatcher Error] {base} skipped — prerequisite "
           f"{', '.join(unmet)} did not run successfully.")
    return {"tool_name": base, "query": "", "output": msg, "success": False, "error": msg}


# ── Async parallel dispatcher ─────────────────────────────────────────────────

async def async_tool_dispatcher(state: AgentState) -> dict:
    """
    Runs queued tools concurrently, honoring PREREQUISITES structurally.

    Dispatch by type: async tool nodes (web_search, sec_edgar, rag_search,
    consensus_estimates) are awaited directly; sync nodes (wikipedia, arxiv)
    run via asyncio.to_thread. asyncio.gather overlaps a wave regardless of type.

    Ordering (ADR-0002): a tool whose prerequisite hasn't succeeded yet does not
    run in the same wave. Prerequisites already satisfied in an earlier iteration
    let the tool run immediately; a prerequisite co-scheduled this batch runs in
    an earlier wave, and its freshly-produced output is threaded into the state
    the dependent tool sees. A missing prerequisite that wasn't queued is
    auto-injected once (self-heal). A prerequisite that ran and FAILED causes the
    dependent tool to be dropped with a loud error rather than compute on bad data.
    """
    queued = [t for t in state.get("tools_remaining", []) if t.split(":")[0] in TOOL_REGISTRY]
    if not queued:
        return {}

    async def _call(tool_spec: str, st: AgentState):
        fn = TOOL_REGISTRY[tool_spec.split(":")[0]]
        if inspect.iscoroutinefunction(fn):
            return await fn(st)
        return await asyncio.to_thread(fn, st)

    # Auto-inject a missing prerequisite that isn't already queued and hasn't
    # run yet — so a dependent tool never runs against un-ingested state.
    queued_bases = {t.split(":")[0] for t in queued}
    seen = _latest_success(state)
    for t in list(queued):
        for p in PREREQUISITES.get(t.split(":")[0], []):
            if p not in queued_bases and p not in seen:
                queued.insert(0, p)          # run before its dependents
                queued_bases.add(p)

    merged: dict = {"tools_remaining": [], "tool_results": [], "tools_called": []}
    work_state = dict(state)
    pending    = list(queued)
    guard      = 0

    while pending and guard <= len(queued):
        guard += 1
        status = _latest_success(work_state)
        ran_ok = {n for n, ok in status.items() if ok}
        failed = {n for n, ok in status.items() if not ok}

        wave, blocked = [], []
        for t in pending:
            prereqs = PREREQUISITES.get(t.split(":")[0], [])
            if any(p in failed for p in prereqs):
                blocked.append((t, [p for p in prereqs if p in failed]))
            elif all(p in ran_ok for p in prereqs):
                wave.append(t)
            # else: a prerequisite is still pending this batch → try a later wave

        # Drop tools whose prerequisite already failed — loud, never silent.
        for t, bad in blocked:
            base = t.split(":")[0]
            merged["tool_results"].append(_dispatcher_error(base, bad))
            merged["tools_called"].append(base)
            pending.remove(t)

        if not wave:
            break

        outputs = await asyncio.gather(*[_call(t, work_state) for t in wave])
        wave_results = []
        for output in outputs:
            if not output:
                continue
            wave_results.extend(output.get("tool_results", []))
            merged["tool_results"].extend(output.get("tool_results", []))
            merged["tools_called"].extend(output.get("tools_called", []))
            # Structured payloads a tool attaches alongside its text output
            # (e.g. the consensus forecast) — merged only when actually produced.
            if output.get("forecast"):
                merged["forecast"] = output["forecast"]

        # Thread this wave's results forward so a dependent tool in a later wave
        # (and _latest_success) sees freshly-produced prerequisite output.
        work_state = {
            **work_state,
            "tool_results": work_state.get("tool_results", []) + wave_results,
        }
        for t in wave:
            pending.remove(t)

    # Anything still pending had a prerequisite that never ran — drop it loudly.
    for t in pending:
        base  = t.split(":")[0]
        unmet = [p for p in PREREQUISITES.get(base, []) if p not in _latest_success(work_state)]
        merged["tool_results"].append(_dispatcher_error(base, unmet))
        merged["tools_called"].append(base)

    return merged


# ── Routers ───────────────────────────────────────────────────────────────────

def route_after_supervisor(state: AgentState) -> str:
    if not state.get("tools_remaining"):
        return END
    return "dispatcher"


def route_after_dispatcher(state: AgentState) -> str:
    if state.get("iteration_count", 0) >= state.get("max_iterations", 8):
        return END
    return "supervisor"


# ── Build graph ───────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("supervisor", supervisor_node)
    builder.add_node("dispatcher", async_tool_dispatcher)

    builder.add_edge(START, "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"dispatcher": "dispatcher", END: END},
    )
    builder.add_conditional_edges(
        "dispatcher",
        route_after_dispatcher,
        {"supervisor": "supervisor", END: END},
    )

    return builder.compile()


graph = build_graph()
