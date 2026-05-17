"""
agent/state.py
--------------
Defines the single source of truth that every node in the LangGraph reads
from and writes to.  Using TypedDict + Annotated lets LangGraph know *how*
to merge concurrent updates (add_messages appends; plain fields overwrite).
"""

from __future__ import annotations

from typing import Annotated, Any
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


# -- Reducers for fields written by parallel tool nodes -----------------------
# When two nodes run concurrently and both return the same key, LangGraph needs
# a reducer function to merge the values instead of raising InvalidUpdateError.

def append_list(existing: list, new: list) -> list:
    """Merge two lists -- for tool_results and tools_called."""
    return (existing or []) + (new or [])



# -- Individual tool result ---------------------------------------------------

class ToolResult(TypedDict):
    """One structured result returned by any tool node."""
    tool_name: str          # e.g. "web_search", "calculator"
    query: str              # the input the tool received
    output: str             # raw text output from the tool
    success: bool           # False if the tool raised an exception
    error: str | None       # populated only when success=False


# -- Financial context extracted by the supervisor ----------------------------

class FinancialContext(TypedDict, total=False):
    """Structured financial metadata accumulated across iterations."""
    ticker: str
    company_name: str
    sector: str
    market_cap: str
    pe_ratio: float | None
    debt_to_equity: float | None
    revenue_growth: str | None
    analyst_sentiment: str  # "bullish" | "bearish" | "neutral"
    key_risks: list[str]


# -- Primary graph state ------------------------------------------------------

class AgentState(TypedDict):
    """
    The complete state object passed between every LangGraph node.

    Fields written by parallel nodes use Annotated reducers so LangGraph
    knows how to merge concurrent updates without raising InvalidUpdateError.
    """

    # Conversation history -- add_messages appends, handles deduplication
    messages: Annotated[list[BaseMessage], add_messages]

    # Current research target
    query: str
    company_target: str

    # Tool execution tracking -- Annotated so parallel nodes can both append
    tool_results: Annotated[list[ToolResult], append_list]
    iteration_count: int
    max_iterations: int

    # Supervisor planning
    current_plan: str
    tools_called: Annotated[list[str], append_list]
    tools_remaining: list[str]

    # Accumulated financial intelligence
    financial_context: FinancialContext

    # Final output
    final_report: str
    error: str | None


# -- Factory: safe default state ----------------------------------------------

def make_initial_state(query: str, max_iterations: int = 8) -> dict[str, Any]:
    """
    Returns a plain dict suitable for graph.invoke().
    LangGraph merges this with its own defaults on first tick.
    """
    return {
        "messages": [],
        "query": query,
        "company_target": "",
        "tool_results": [],
        "iteration_count": 0,
        "max_iterations": max_iterations,
        "current_plan": "",
        "tools_called": [],
        "tools_remaining": [],
        "financial_context": {},
        "final_report": "",
        "error": None,
    }