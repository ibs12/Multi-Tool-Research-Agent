"""
agent/graph.py
--------------
Assembles the LangGraph StateGraph.

Improvement 3 -- Async parallel tool execution:
  Tools that don't depend on each other (e.g. web_search + wikipedia) now
  run concurrently via asyncio.gather(). The supervisor queues multiple
  tools; the async_tool_dispatcher runs them in parallel and merges results
  before returning to the supervisor.

  Sequential:  web_search(8s) -> wikipedia(3s) = 11s total
  Parallel:    web_search(8s)
               wikipedia(3s)  } asyncio.gather = 8s total
  ~30% latency reduction on typical 2-tool iterations.

Graph topology:
    [START] -> supervisor -> async_tool_dispatcher -> supervisor (loop)
                                                   -> synthesis -> [END]
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.nodes.supervisor import supervisor_node
from agent.nodes.synthesis import synthesis_node
from agent.nodes.tools import (
    web_search_node,
    wikipedia_node,
    calculator_node,
    arxiv_node,
    sec_edgar_node,
    rag_search_node,
)

load_dotenv()

# Map tool names -> synchronous node functions
TOOL_REGISTRY = {
    "web_search":  web_search_node,
    "wikipedia":   wikipedia_node,
    "calculator":  calculator_node,
    "arxiv":       arxiv_node,
    "sec_edgar":   sec_edgar_node,
    "rag_search":  rag_search_node,
}


# -- Improvement 3: Async parallel dispatcher ---------------------------------

def async_tool_dispatcher(state: AgentState) -> dict:
    """
    Runs all queued tools concurrently using asyncio.gather().

    Each tool function is synchronous (CPU/IO bound), so we run them in a
    ThreadPoolExecutor to avoid blocking the event loop. Results are merged
    by the append_list reducers in AgentState.

    Interview talking point:
      "Tool functions are synchronous because external APIs use blocking I/O.
       We use run_in_executor to run them in a thread pool so asyncio.gather
       can overlap their wait times. This is the correct pattern for
       parallelising sync I/O in an async context."
    """
    remaining = state.get("tools_remaining", [])
    valid_tools = [t for t in remaining if t.split(":")[0] in TOOL_REGISTRY]

    if not valid_tools:
        return {}

    async def _run_all():
        loop = asyncio.get_event_loop()
        executor = ThreadPoolExecutor(max_workers=len(valid_tools))

        async def _run_one(tool_name: str):
            # Get base name (handles "calculator:pe_ratio:..." keys)
            base = tool_name.split(":")[0]
            fn = TOOL_REGISTRY[base]
            # Run the sync function in a thread
            return await loop.run_in_executor(executor, fn, state)

        results = await asyncio.gather(*[_run_one(t) for t in valid_tools])
        executor.shutdown(wait=False)
        return results

    # Run the async gather from sync context (LangGraph nodes are sync)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in an event loop (e.g. Jupyter) — use nest_asyncio pattern
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, _run_all())
                tool_outputs = future.result()
        else:
            tool_outputs = loop.run_until_complete(_run_all())
    except RuntimeError:
        tool_outputs = asyncio.run(_run_all())

    # Merge all tool outputs into a single state delta
    merged: dict = {
        "tools_remaining": [],   # all tools consumed
        "tool_results": [],
        "tools_called": [],
    }
    for output in tool_outputs:
        if not output:
            continue
        merged["tool_results"].extend(output.get("tool_results", []))
        merged["tools_called"].extend(output.get("tools_called", []))

    return merged


# -- Router -------------------------------------------------------------------

def route_after_dispatcher(state: AgentState) -> str:
    """
    After the dispatcher runs all queued tools, decide what's next.
    tools_remaining is cleared by the dispatcher, so always return
    to supervisor — it will decide whether to queue more tools or synthesise.
    """
    if state.get("iteration_count", 0) >= state.get("max_iterations", 8):
        return "synthesis"
    return "supervisor"


def route_after_supervisor(state: AgentState) -> str:
    """
    After supervisor plans, check if there are tools to run.
    """
    if not state.get("tools_remaining"):
        return "synthesis"
    return "dispatcher"


# -- Build graph --------------------------------------------------------------

def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("supervisor",  supervisor_node)
    builder.add_node("dispatcher",  async_tool_dispatcher)
    builder.add_node("synthesis",   synthesis_node)

    builder.add_edge(START, "supervisor")

    builder.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {"dispatcher": "dispatcher", "synthesis": "synthesis"},
    )

    builder.add_conditional_edges(
        "dispatcher",
        route_after_dispatcher,
        {"supervisor": "supervisor", "synthesis": "synthesis"},
    )

    builder.add_edge("synthesis", END)

    return builder.compile()


graph = build_graph()