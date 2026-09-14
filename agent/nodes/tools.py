"""
agent/nodes/tools.py
────────────────────
LangGraph tool nodes — one function per tool.

Each node:
  1. Reads the current query / company target from state
  2. Calls the corresponding tool implementation
  3. Returns a *partial* state dict that LangGraph merges back in

Async nodes (web_search, sec_edgar, rag_search) are awaited directly by the
async dispatcher.  Sync nodes (wikipedia, calculator, arxiv) are wrapped with
asyncio.to_thread() in the dispatcher so they don't block the event loop.

Key design rule: nodes never raise.  All errors are caught, wrapped in a
ToolResult with success=False, and returned so the supervisor can decide
whether to retry or proceed with partial data.
"""

from __future__ import annotations

import datetime

import asyncio

from agent.state import AgentState, ToolResult
from tools.wikipedia import run_wikipedia, WIKIPEDIA_ERROR
from tools.arxiv_search import run_arxiv_search, ARXIV_ERROR


# ── Shared helpers ────────────────────────────────────────────────────────────

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


# ── Web Search Node (async) ───────────────────────────────────────────────────

async def web_search_node(state: AgentState) -> dict:
    from tools.web_search import run_web_search, WEBSEARCH_ERROR

    target = state.get("company_target", state["query"])
    year   = datetime.datetime.now().year
    query  = f"{target} stock earnings analyst sentiment outlook {year - 1} {year}"

    output  = await run_web_search(query, financial_only=True)
    success = not output.startswith(WEBSEARCH_ERROR)

    # Delta only: return just this tool's new result. The append_list reducer
    # accumulates across the batch/run — returning the full prior list here
    # would double-count it (see ADR-notes / issue #2).
    return {
        "tool_results": [_build_result("web_search", query, output, success,
                                       error=None if success else output)],
        "tools_called": ["web_search"],
    }


# ── Wikipedia Node (sync) ─────────────────────────────────────────────────────

def wikipedia_node(state: AgentState) -> dict:
    target  = state.get("company_target", state["query"])
    output  = run_wikipedia(target)
    success = not output.startswith(WIKIPEDIA_ERROR)

    return {
        "tool_results": [_build_result("wikipedia", target, output, success,
                                       error=None if success else output)],
        "tools_called": ["wikipedia"],
    }


# Note: there is no calculator tool node. Financial ratios are computed inline
# by the supervisor via the calculate_ratio tool (agent/nodes/supervisor.py),
# on numbers it has already extracted from tool results — so it is correctly
# ordered by construction and needs no dispatcher scheduling.


# ── ArXiv Node (sync) ─────────────────────────────────────────────────────────

def arxiv_node(state: AgentState) -> dict:
    target  = state.get("company_target", state["query"])
    fin_ctx = state.get("financial_context", {})
    sector  = fin_ctx.get("sector", "finance")
    query   = f"{sector} risk modelling machine learning {target}"

    output  = run_arxiv_search(query)
    success = not output.startswith(ARXIV_ERROR)

    return {
        "tool_results": [_build_result("arxiv", query, output, success,
                                       error=None if success else output)],
        "tools_called": ["arxiv"],
    }


# ── SEC EDGAR Node (async) ────────────────────────────────────────────────────

async def sec_edgar_node(state: AgentState) -> dict:
    from tools.sec_edgar import run_sec_search, SEC_EDGAR_ERROR

    target  = state.get("company_target", state["query"])
    output  = await run_sec_search(target)
    success = not output.startswith(SEC_EDGAR_ERROR)

    return {
        "tool_results": [_build_result("sec_edgar", target, output, success,
                                       error=None if success else output)],
        "tools_called": ["sec_edgar"],
    }


# ── RAG Search Node (async) ───────────────────────────────────────────────────

async def rag_search_node(state: AgentState) -> dict:
    from tools.rag_search import run_rag_pipeline, RAG_ERROR
    import re as _re

    target  = state.get("company_target", state["query"])
    query   = f"revenue earnings EPS net income profit margin {target}"
    company_clean = _re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', target).strip()

    output  = await run_rag_pipeline(
        query_text=query,
        tool_results=state.get("tool_results", []),
        company=company_clean,
    )
    success = not output.startswith(RAG_ERROR)

    return {
        "tool_results": [_build_result("rag_search", query, output, success,
                                       error=None if success else output)],
        "tools_called": ["rag_search"],
    }


# ── Consensus Estimates Node (async wrapper around sync yfinance) ─────────────

async def consensus_estimates_node(state: AgentState) -> dict:
    """
    Fetches analyst EPS and revenue consensus estimates from Yahoo Finance.

    Requires a ticker symbol to be present in company_target (the supervisor
    sets this as 'Company Name (TICK)').  Returns a failed result immediately
    if no ticker can be extracted — never guesses a ticker.
    """
    import re as _re
    from tools.consensus_estimates import run_consensus_estimates, CONSENSUS_ERROR, HISTORICAL_ERROR

    target = state.get("company_target", state["query"])

    ticker_match = _re.search(r'\(([A-Z]{1,5})\)', target)
    if not ticker_match:
        msg = (
            "[Consensus Estimates Error] No ticker symbol found in company_target "
            f"('{target}'). Format must be 'Company Name (TICKER)'."
        )
        return {
            "tool_results": [_build_result("consensus_estimates", target, msg,
                                           False, error=msg)],
            "tools_called": ["consensus_estimates"],
        }

    ticker = ticker_match.group(1)
    # All three yfinance calls are sync HTTP I/O — run concurrently in threads.
    # build_quarterly_outlook returns structured data for the forecast chart
    # rather than prose for the report.
    from tools.consensus_estimates import build_quarterly_outlook, run_historical_financials
    consensus_out, historical_out, outlook = await asyncio.gather(
        asyncio.to_thread(run_consensus_estimates, ticker),
        asyncio.to_thread(run_historical_financials, ticker),
        asyncio.to_thread(build_quarterly_outlook, ticker),
    )

    combined = consensus_out + "\n\n" + historical_out
    # Succeed if at least one of the two fetches worked
    success = not (
        consensus_out.startswith(CONSENSUS_ERROR) and
        historical_out.startswith(HISTORICAL_ERROR)
    )

    return {
        "tool_results": [_build_result("consensus_estimates", ticker, combined, success,
                                       error=None if success else combined)],
        "tools_called": ["consensus_estimates"],
        "forecast":     outlook,
    }
