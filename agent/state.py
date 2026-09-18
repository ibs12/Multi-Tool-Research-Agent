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


def merge_handoff(existing: dict, new: dict) -> dict:
    """Shallow-merge the nested handoff contract (ADR-0007).

    The agents hand off sequentially (research → risk → compliance), each
    writing only its OWN key. A plain overwrite would drop the prior agent's
    field every transition, so this shallow-merges: each node returns just
    ``{"handoff": {"risk_assessment": ...}}`` and the reducer folds it in.
    Single-writer-per-key means there is never a real conflict to resolve.
    """
    return {**(existing or {}), **(new or {})}



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
    """Financial metadata carried across iterations.

    Only ``sector`` is currently written (by the supervisor) and read (by the
    arxiv node). Speculative fields were removed (issue #10) — add one back when
    a tool actually writes it and a consumer reads it, not on spec (ADR-0001).
    """
    sector: str


# -- Cross-agent handoff contract (ADR-0007) ----------------------------------
# The single-writer contract the three agents pass along the research → risk →
# compliance pipeline. Each field is written by exactly one agent and read by
# the next; the nested shape (vs. loose top-level fields) is what keeps it from
# drifting into a grab-bag. Written via the ``merge_handoff`` reducer so each
# agent contributes only its own key.

class ComplianceVerdict(TypedDict, total=False):
    """The compliance-checker's terminal judgement."""
    verdict: str                 # "clear" | "needs-revision" | "escalate"
    reasons: list[str]           # why — citation gaps, fabrication, regulatory flags
    gap_type: str | None         # missing_disclosure | conflicting_sources |
                                 # unverifiable_citation | regulatory_flag | None


class EscalationPackage(TypedDict, total=False):
    """Structured hand-to-a-human payload (ADR-0009).

    Deliberately interrupt-shaped: these are the same fields an ``interrupt()``
    payload would carry, so upgrading to pause/resume later is swapping the
    terminal END for interrupt() with the *same object*, not a redesign.
    """
    reason: str                  # human-readable summary of why this escalated
    unresolved: list[str]        # what a human must decide
    evidence_summary: str        # one-glance context for the human
    research_findings: Any       # accumulated corpus carried into the decision
    risk_assessment: Any
    compliance_reasons: list[str]


class HandoffContract(TypedDict, total=False):
    research_findings: Any                 # written by research, read by risk
    risk_assessment: Any                   # written by risk, read by compliance
    compliance_verdict: ComplianceVerdict  # written by compliance
    escalation: EscalationPackage          # written on the escalate path only


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

    # -- Multi-agent extension (Map 1) ----------------------------------------
    # The research-loop internals above ARE the "research_loop" of ADR-0007 —
    # kept top-level and unchanged so the reused supervisor/dispatcher loop
    # needs no rewrite. The NEW cross-agent contract is nested under `handoff`
    # (merge_handoff reducer) so sequential agents each write only their key.
    handoff: Annotated[HandoffContract, merge_handoff]

    # Which pipeline this run exercises: "single" = research → synthesis (the
    # pre-multi-agent ablation baseline, E5); "multi" = research → risk →
    # compliance. One compiled graph, switched at runtime by this field so both
    # arms run on the identical substrate.
    agent_mode: str

    # Structured rest-of-year forecast (charted directly by the frontend,
    # never routed through the LLM) — None when consensus data is unavailable
    forecast: dict[str, Any] | None

    # Final output
    final_report: str
    error: str | None

    # Why the run stopped (issue #6):
    #   "completed"                  — supervisor signalled ready_to_synthesise
    #   "iteration_budget_exhausted" — hit max_iterations mid-research
    #   "no_new_tools"               — supervisor had no un-run tools left to plan
    #   "escalated"                  — compliance-checker sent it to a human;
    #                                  no brief, EscalationPackage in handoff (ADR-0009)
    termination_reason: str | None


# -- Factory: safe default state ----------------------------------------------

def make_initial_state(
    query: str, max_iterations: int = 8, agent_mode: str = "multi"
) -> dict[str, Any]:
    """
    Returns a plain dict suitable for graph.invoke().
    LangGraph merges this with its own defaults on first tick.

    ``agent_mode`` selects the pipeline: "multi" (research → risk → compliance)
    or "single" (research → synthesis, the pre-multi-agent baseline). Entry
    points pass it from the AGENT_MODE env var (ADR-0006/0007, E5).
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
        "handoff": {},
        "agent_mode": agent_mode,
        "forecast": None,
        "final_report": "",
        "error": None,
        "termination_reason": None,
    }