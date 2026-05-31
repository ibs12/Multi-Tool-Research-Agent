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
    calculator_node,
    arxiv_node,
    sec_edgar_node,
    rag_search_node,
    consensus_estimates_node,
)

load_dotenv()

TOOL_REGISTRY = {
    "web_search":           web_search_node,
    "wikipedia":            wikipedia_node,
    "calculator":           calculator_node,
    "arxiv":                arxiv_node,
    "sec_edgar":            sec_edgar_node,
    "rag_search":           rag_search_node,
    "consensus_estimates":  consensus_estimates_node,
}


# ── Async parallel dispatcher ─────────────────────────────────────────────────

async def async_tool_dispatcher(state: AgentState) -> dict:
    """
    Runs all queued tools concurrently using asyncio.gather().

    Async tool nodes (web_search, sec_edgar, rag_search) are awaited
    directly.  Sync tool nodes (wikipedia, calculator, arxiv) are wrapped
    with asyncio.to_thread so their blocking I/O doesn't stall the loop.
    A fixed pool of at most 4 threads handles the sync tools.

    Interview talking point:
      "Tool functions use a mix of async I/O and blocking sync libraries.
       asyncio.iscoroutinefunction lets me dispatch each tool correctly
       without any manual routing — async tools run natively in the loop,
       sync tools run in a thread pool.  asyncio.gather overlaps all of
       them regardless of type."
    """
    remaining   = state.get("tools_remaining", [])
    valid_tools = [t for t in remaining if t.split(":")[0] in TOOL_REGISTRY]

    if not valid_tools:
        return {}

    async def _call(tool_spec: str):
        fn = TOOL_REGISTRY[tool_spec.split(":")[0]]
        if inspect.iscoroutinefunction(fn):
            return await fn(state)
        return await asyncio.to_thread(fn, state)

    tool_outputs = await asyncio.gather(*[_call(t) for t in valid_tools])

    merged: dict = {"tools_remaining": [], "tool_results": [], "tools_called": []}
    for output in tool_outputs:
        if not output:
            continue
        merged["tool_results"].extend(output.get("tool_results", []))
        merged["tools_called"].extend(output.get("tools_called", []))

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
