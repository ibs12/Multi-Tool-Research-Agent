"""
agent/nodes/tools.py
────────────────────
LangGraph tool nodes — one function per tool.

Each node:
  1. Reads the current query / company target from state
  2. Calls the corresponding tool implementation
  3. Returns a *partial* state dict that LangGraph merges back in

Key design rule: nodes never raise.  All errors are caught, wrapped in a
ToolResult with success=False, and returned so the supervisor can decide
whether to retry or proceed with partial data.

Parallel execution:
  LangGraph runs whichever of these nodes are in tools_remaining concurrently.
  Because each node only appends to tool_results and pops its own name from
  tools_remaining, there are no write conflicts between parallel nodes.
"""

from __future__ import annotations

import datetime

from agent.state import AgentState, ToolResult
from tools.web_search import run_web_search
from tools.calculator import run_calculator
from tools.wikipedia import run_wikipedia
from tools.arxiv_search import run_arxiv_search


# ── Shared helper ─────────────────────────────────────────────────────────────

def _build_result(
    tool_name: str,
    query: str,
    output: str,
    success: bool,
    error: str | None = None,
) -> ToolResult:
    return ToolResult(
        tool_name=tool_name,
        query=query,
        output=output,
        success=success,
        error=error,
    )


def _pop_tool(state: AgentState, tool_name: str) -> list[str]:
    """Remove this tool from the remaining queue."""
    return [t for t in state.get("tools_remaining", []) if t != tool_name]


# ── Web Search Node ───────────────────────────────────────────────────────────

def web_search_node(state: AgentState) -> dict:
    """
    Searches for recent news, earnings reports, and analyst sentiment.

    Query strategy: combines the company target with financial keywords
    to bias Tavily toward actionable financial content.
    """
    target = state.get("company_target", state["query"])
    year = datetime.datetime.now().year
    query = f"{target} stock earnings analyst sentiment outlook {year - 1} {year}"

    output = run_web_search(query, financial_only=True)
    success = not output.startswith("[WebSearch Error]")

    result = _build_result(
        tool_name="web_search",
        query=query,
        output=output,
        success=success,
        error=None if success else output,
    )

    return {
        "tool_results": state.get("tool_results", []) + [result],
        "tools_called": state.get("tools_called", []) + ["web_search"],
        "tools_remaining": _pop_tool(state, "web_search"),
    }


# ── Wikipedia Node ────────────────────────────────────────────────────────────

def wikipedia_node(state: AgentState) -> dict:
    """
    Fetches stable company background: sector, business model, history.
    Uses the company_target set by the supervisor node.
    """
    target = state.get("company_target", state["query"])
    output = run_wikipedia(target)
    success = not output.startswith("[Wikipedia Error]")

    result = _build_result(
        tool_name="wikipedia",
        query=target,
        output=output,
        success=success,
        error=None if success else output,
    )

    return {
        "tool_results": state.get("tool_results", []) + [result],
        "tools_called": state.get("tools_called", []) + ["wikipedia"],
        "tools_remaining": _pop_tool(state, "wikipedia"),
    }


# ── Calculator Node ───────────────────────────────────────────────────────────

def calculator_node(state: AgentState) -> dict:
    """
    Computes financial ratios from figures extracted by the supervisor.

    The supervisor encodes what to calculate in tools_remaining as either:
      - "calculator"                          → runs a default ratio suite
      - "calculator:pe_ratio:price=X eps=Y"  → targeted ratio call

    This node reads the extended instruction if present.
    """
    raw_instruction = next(
        (t for t in state.get("tools_remaining", []) if t.startswith("calculator:")),
        None,
    )

    if raw_instruction:
        calc_query = raw_instruction[len("calculator:"):].strip()
    else:
        fin_ctx = state.get("financial_context", {})
        calc_query = _build_default_query(fin_ctx)

    output = run_calculator(calc_query)
    success = not output.startswith("[Calculator Error]")

    result = _build_result(
        tool_name="calculator",
        query=calc_query,
        output=output,
        success=success,
        error=None if success else output,
    )

    remaining = [
        t for t in state.get("tools_remaining", [])
        if not t.startswith("calculator")
    ]

    return {
        "tool_results": state.get("tool_results", []) + [result],
        "tools_called": state.get("tools_called", []) + ["calculator"],
        "tools_remaining": remaining,
    }


def _build_default_query(fin_ctx: dict) -> str:
    """Build a sensible default calculation from accumulated financial context."""
    pe = fin_ctx.get("pe_ratio")
    if pe:
        return f"expression: {pe}"
    return "expression: 100 * 1.08 ** 5"


# ── ArXiv Node ────────────────────────────────────────────────────────────────

def arxiv_node(state: AgentState) -> dict:
    """
    Searches for recent academic papers relevant to the company's sector.

    Query strategy: uses the sector (if known) + AI/risk keywords so results
    are academically grounded rather than company-specific news.
    """
    target = state.get("company_target", state["query"])
    fin_ctx = state.get("financial_context", {})
    sector = fin_ctx.get("sector", "finance")

    query = f"{sector} risk modelling machine learning {target}"

    output = run_arxiv_search(query)
    success = not output.startswith("[ArXiv Error]")

    result = _build_result(
        tool_name="arxiv",
        query=query,
        output=output,
        success=success,
        error=None if success else output,
    )

    return {
        "tool_results": state.get("tool_results", []) + [result],
        "tools_called": state.get("tools_called", []) + ["arxiv"],
        "tools_remaining": _pop_tool(state, "arxiv"),
    }

# -- SEC EDGAR Node -----------------------------------------------------------

def sec_edgar_node(state: AgentState) -> dict:
    """
    Fetches primary source SEC filings: 10-K, 10-Q, 8-K.

    This is the gold standard for financial data — actual filed numbers
    rather than analyst summaries. Particularly useful for:
      - Verifying revenue/earnings figures found in web search
      - Finding risk factor disclosures (10-K Item 1A)
      - Tracking insider transactions and ownership changes
    """
    from tools.sec_edgar import run_sec_search

    target = state.get("company_target", state["query"])
    output = run_sec_search(target)
    success = not output.startswith("[SEC EDGAR Error]")

    result = _build_result(
        tool_name="sec_edgar",
        query=target,
        output=output,
        success=success,
        error=None if success else output,
    )

    return {
        "tool_results": [result],
        "tools_called": ["sec_edgar"],
        "tools_remaining": _pop_tool(state, "sec_edgar"),
    }

# -- RAG Search Node ----------------------------------------------------------

def rag_search_node(state: AgentState) -> dict:
    """
    RAG pipeline node — ingests SEC filing documents and runs semantic search.

    Runs AFTER sec_edgar so it has filing URLs to fetch.
    Adds retrieved passages to tool_results for synthesis to cite.

    Two-phase:
      1. Parse filing URLs from existing sec_edgar tool results in state
      2. Fetch, chunk, embed, store in ChromaDB, then query semantically
    """
    from tools.rag_search import run_rag_pipeline

    target    = state.get("company_target", state["query"])
    fin_ctx   = state.get("financial_context", {})

    # Build a targeted financial query from what the supervisor knows
    sector    = fin_ctx.get("sector", "")
    query     = f"revenue earnings EPS net income profit margin {target}"

    # Strip ticker suffix before passing to ChromaDB filter
    # ChromaDB stores company as "Apple Inc." but state has "Apple Inc. (AAPL)"
    import re as _re
    company_clean = _re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', target).strip()

    output = run_rag_pipeline(
        query_text=query,
        tool_results=state.get("tool_results", []),
        company=company_clean,
    )

    success = not output.startswith("[RAG Error]")

    result = _build_result(
        tool_name="rag_search",
        query=query,
        output=output,
        success=success,
        error=None if success else output,
    )

    return {
        "tool_results": [result],
        "tools_called": ["rag_search"],
        "tools_remaining": _pop_tool(state, "rag_search"),
    }