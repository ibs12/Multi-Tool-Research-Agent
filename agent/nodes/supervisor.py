"""
agent/nodes/supervisor.py
--------------------------
The supervisor uses the Anthropic native tool-use API.

Improvement 1 -- Native tool-use API (replaces string-encoded calls):
  Instead of asking Claude to output "calculator:pe_ratio: stock_price=X eps=Y"
  as a string, we define typed tool schemas and let Claude call them directly.
  The API returns structured tool_use blocks with validated parameters.
  This is more robust, self-documenting, and the correct production pattern.

Improvement 3 -- Async-ready design:
  The supervisor returns a tools_remaining list that the async dispatcher
  in graph.py will execute concurrently using asyncio.gather().

ReAct pattern:
  Each supervisor call = Reason (plan field) + Act (tools_to_call).
  LangGraph's stateful loop handles the Observe step by accumulating
  tool results in AgentState between iterations.
"""

from __future__ import annotations

import json
import os
import re
from dotenv import load_dotenv
import anthropic

from agent.state import AgentState

load_dotenv()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 1024

# -- Improvement 1: Native tool schemas ---------------------------------------
# These are passed to the Anthropic API as typed function signatures.
# Claude selects which tools to call and fills in the parameters — no more
# string parsing of "calculator:pe_ratio: stock_price=X eps=Y".

TOOL_SCHEMAS = [
    {
        "name": "plan_research",
        "description": (
            "Plan the next research step. Call this to specify which tools "
            "to run next and why."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company_target": {
                    "type": "string",
                    "description": "Full company name and ticker, e.g. 'Apple Inc. (AAPL)'",
                },
                "sector": {
                    "type": "string",
                    "description": "Industry sector, e.g. 'Technology', 'Banking'",
                },
                "reasoning": {
                    "type": "string",
                    "description": "2-3 sentences: what you know so far and what gaps remain",
                },
                "tools_to_call": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["web_search", "wikipedia", "sec_edgar", "arxiv", "calculator"],
                    },
                    "description": "Tools to invoke next. Keep to 1-2 per iteration.",
                },
                "ready_to_synthesise": {
                    "type": "boolean",
                    "description": "True when enough data has been collected for a full analyst brief",
                },
            },
            "required": ["company_target", "reasoning", "tools_to_call", "ready_to_synthesise"],
        },
    },
    {
        "name": "calculate_ratio",
        "description": (
            "Calculate a financial ratio from figures found in research. "
            "Use this when you have extracted specific numbers from tool results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ratio_type": {
                    "type": "string",
                    "enum": ["pe_ratio", "debt_to_equity", "cagr", "revenue_growth",
                             "profit_margin", "ev_to_ebitda", "expression"],
                    "description": "The financial ratio to compute",
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "Key-value pairs for the ratio. Examples:\n"
                        "pe_ratio: {stock_price: 182.5, eps: 6.13}\n"
                        "revenue_growth: {current: 120.5, previous: 108.2}\n"
                        "expression: {expr: '150 / 12.5'}"
                    ),
                },
            },
            "required": ["ratio_type", "parameters"],
        },
    },
]

SYSTEM_PROMPT = """You are a senior financial research analyst AI supervisor.

Your job is to coordinate a multi-tool research agent investigating a company or financial topic.

You have two tools available:
1. plan_research — specify which tools to run next
2. calculate_ratio — compute a financial ratio from numbers you've already found

Rules:
- Always call plan_research to specify your next steps
- Call calculate_ratio ONLY when you have extracted actual numbers from tool results
- Never repeat a tool already in tools_called
- On the first iteration, always include web_search and wikipedia
- Available research tools: web_search, wikipedia, sec_edgar, arxiv, calculator
- Set ready_to_synthesise=true when you have enough for a complete analyst brief
- sec_edgar provides primary source filings (10-K/10-Q) — use it for verified financials
"""


# -- Main node ----------------------------------------------------------------

def supervisor_node(state: AgentState) -> dict:
    """
    Calls Claude using the native tool-use API.
    Claude returns structured tool_use blocks — no JSON string parsing needed.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_message = _build_user_message(state)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            tool_choice={"type": "any"},   # Claude must call at least one tool
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError as e:
        return {
            "iteration_count": state.get("iteration_count", 0) + 1,
            "tools_remaining": [],
            "current_plan": f"Supervisor API error: {e}",
            "error": str(e),
        }

    # -- Parse tool_use blocks from response ----------------------------------
    plan = {}
    inline_calc_results = []

    for block in response.content:
        if block.type != "tool_use":
            continue

        if block.name == "plan_research":
            plan = block.input   # already a dict — no JSON parsing needed!

        elif block.name == "calculate_ratio":
            # Claude called calculate_ratio directly with typed params
            result = _execute_inline_calc(block.input)
            inline_calc_results.append(result)

    # Fallback if no plan_research block returned
    if not plan:
        plan = {
            "company_target": state.get("company_target", ""),
            "reasoning": "No plan returned — proceeding to synthesis.",
            "tools_to_call": [],
            "ready_to_synthesise": True,
        }

    # Defensive filter: strip tools already called
    already_called = set(state.get("tools_called", []))
    planned_tools = [t for t in plan.get("tools_to_call", []) if t not in already_called]

    # Update financial context
    fin_ctx = dict(state.get("financial_context", {}))
    if plan.get("sector"):
        fin_ctx["sector"] = plan["sector"]

    # Merge any inline calculator results into tool_results
    existing_results = state.get("tool_results", [])
    all_results = existing_results + inline_calc_results

    return {
        "iteration_count": state.get("iteration_count", 0) + 1,
        "company_target": plan.get("company_target", state.get("company_target", "")),
        "current_plan": plan.get("reasoning", ""),
        "tools_remaining": [] if plan.get("ready_to_synthesise") else planned_tools,
        "financial_context": fin_ctx,
        "tool_results": inline_calc_results,   # append_list reducer handles merge
        "tools_called": ["calculator"] if inline_calc_results else [],
    }


# -- Inline calculator execution ----------------------------------------------

def _execute_inline_calc(tool_input: dict) -> dict:
    """
    Execute a calculate_ratio call made directly by the supervisor.
    Returns a ToolResult dict for inclusion in agent state.
    """
    from tools.calculator import run_calculator

    ratio_type = tool_input.get("ratio_type", "expression")
    params = tool_input.get("parameters", {})

    if ratio_type == "expression":
        query = f"expression: {params.get('expr', '0')}"
    else:
        param_str = " ".join(f"{k}={v}" for k, v in params.items())
        query = f"{ratio_type}: {param_str}"

    output = run_calculator(query)
    success = not output.startswith("[Calculator Error]")

    return {
        "tool_name": "calculator",
        "query": query,
        "output": output,
        "success": success,
        "error": None if success else output,
    }


# -- Message builder ----------------------------------------------------------

def _build_user_message(state: AgentState) -> str:
    lines = [
        f"RESEARCH QUERY: {state['query']}",
        f"ITERATION: {state.get('iteration_count', 0) + 1} / {state.get('max_iterations', 8)}",
        f"TOOLS ALREADY CALLED: {', '.join(state.get('tools_called', [])) or 'none'}",
        "",
        "TOOL RESULTS SO FAR:",
    ]

    results = state.get("tool_results", [])
    if not results:
        lines.append("  (none yet)")
    else:
        for r in results:
            status = "SUCCESS" if r["success"] else "FAILED"
            preview = r["output"][:800] + ("..." if len(r["output"]) > 800 else "")
            lines += [
                f"  [{status}] {r['tool_name'].upper()} — query: '{r['query']}'",
                f"  {preview}",
                "",
            ]

    lines.append("Call plan_research to specify next steps.")
    return "\n".join(lines)