"""
agent/graph.py
--------------
Assembles the LangGraph StateGraph for the research loop.

Topology:
    [START] → supervisor → dispatcher → supervisor (loops)
                        ↘                          ↘
                         synthesis (END)            synthesis (END)

Synthesis is NOT a node in this graph — it is called separately by both
run.py (CLI) and api/main.py (API) after the graph completes.  This lets
the streaming endpoint deliver synthesis tokens one-by-one in real time
without needing to intercept LangGraph's internal node execution.

Parallel tool execution:
    The async_tool_dispatcher runs all queued tools concurrently via
    asyncio.gather() + ThreadPoolExecutor.  Tool functions are sync
    (blocking I/O), so run_in_executor overlaps their wait times.
    Typical 2-tool iteration: 11 s sequential → 8 s parallel.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
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
)

load_dotenv()

# Map tool names → synchronous node functions
TOOL_REGISTRY = {
    "web_search": web_search_node,
    "wikipedia":  wikipedia_node,
    "calculator": calculator_node,
    "arxiv":      arxiv_node,
    "sec_edgar":  sec_edgar_node,
    "rag_search": rag_search_node,
}


# ── Async parallel dispatcher ─────────────────────────────────────────────────

def async_tool_dispatcher(state: AgentState) -> dict:
    """
    Runs all queued tools concurrently using asyncio.gather().

    Each tool function is synchronous (blocking I/O), so run_in_executor
    lets asyncio overlap their wait times without blocking the event loop.
    asyncio.run() is used directly — LangGraph calls nodes from a plain
    thread (not an async context), so a fresh event loop is always safe.

    Interview talking point:
      "Tool functions use blocking I/O (HTTP, subprocess). I run them in
       a ThreadPoolExecutor so asyncio.gather can overlap wait times.
       asyncio.run() is correct here because LangGraph invokes nodes
       synchronously from a worker thread — there is no running loop."
    """
    remaining  = state.get("tools_remaining", [])
    valid_tools = [t for t in remaining if t.split(":")[0] in TOOL_REGISTRY]

    if not valid_tools:
        return {}

    async def _run_all():
        executor = ThreadPoolExecutor(max_workers=len(valid_tools))
        try:
            tasks = [
                asyncio.get_event_loop().run_in_executor(
                    executor, TOOL_REGISTRY[t.split(":")[0]], state
                )
                for t in valid_tools
            ]
            return await asyncio.gather(*tasks)
        finally:
            executor.shutdown(wait=False)

    tool_outputs = asyncio.run(_run_all())

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
