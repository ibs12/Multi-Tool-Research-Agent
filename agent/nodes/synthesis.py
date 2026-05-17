"""
agent/nodes/synthesis.py
────────────────────────
The synthesis node is the agent's final step — it receives ALL accumulated
tool results and writes a structured analyst brief using Claude.

Design decisions:
  - Separate Claude call from the supervisor so prompts stay focused
  - Full tool output is passed in (not truncated) — synthesis needs detail
  - Report is structured with fixed sections so it's easy to parse downstream
  - Citations are tied to specific tool sources for auditability

Interview talking point:
  Keeping synthesis separate from the supervisor is an example of the
  "single responsibility" principle applied to LLM nodes.  The supervisor
  reasons about *what to do*; synthesis reasons about *what to say*.
  Mixing them would make both prompts worse.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv
import anthropic

from agent.state import AgentState

load_dotenv()

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 2048

SYSTEM_PROMPT = """You are a senior financial analyst at a global investment bank.

You have been given raw research data collected by an AI agent across multiple sources.
Your job is to synthesise this into a concise, professional analyst brief.

Structure your report with EXACTLY these sections, using these markdown headers:

## Executive Summary
2-3 sentences. Company, sector, overall investment stance.

## Company Overview
Key facts: founded, HQ, business model, market position. Source: Wikipedia.

## Recent Developments
Latest news, earnings, strategic moves. Source: web search results.

## Financial Snapshot
Any ratios or figures found (P/E, revenue growth, margins, D/E).
If calculator results are available, include them here.
If no figures were found, state that explicitly — do not fabricate numbers.

## Risk Factors
3-5 bullet points. Draw from news sentiment, sector context, and academic research.

## Academic & Research Context
Cite any relevant arXiv papers found. If none, omit this section.

## Analyst Verdict
Bullish / Neutral / Bearish with a one-paragraph justification.

Rules:
- NEVER invent financial figures. If data is missing, say so.
- Cite your source for each claim: [Web Search], [Wikipedia], [ArXiv], [Calculator]
- Use professional financial language throughout
- Keep the total report under 600 words
"""


def synthesis_node(state: AgentState) -> dict:
    """
    Calls Claude with the full research corpus to produce the final report.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    user_message = _build_synthesis_prompt(state)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        report = response.content[0].text.strip()

    except anthropic.APIError as e:
        report = _fallback_report(state, error=str(e))

    return {
        "final_report": report,
        "error": None,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_synthesis_prompt(state: AgentState) -> str:
    """
    Builds the full research corpus for Claude to synthesise.
    Unlike the supervisor prompt, we pass the FULL tool output here —
    synthesis needs all the detail to write accurate citations.
    """
    lines = [
        f"RESEARCH QUERY: {state['query']}",
        f"COMPANY TARGET: {state.get('company_target', 'Unknown')}",
        f"SUPERVISOR REASONING: {state.get('current_plan', 'N/A')}",
        "",
        "=" * 60,
        "FULL TOOL RESULTS:",
        "=" * 60,
        "",
    ]

    results = state.get("tool_results", [])
    if not results:
        lines.append("No tool results were collected.")
    else:
        for r in results:
            status = "✓ SUCCESS" if r["success"] else "✗ FAILED"
            lines += [
                f"[{r['tool_name'].upper()}] {status}",
                f"Query: {r['query']}",
                "",
                r["output"] if r["success"] else f"Error: {r.get('error', 'unknown')}",
                "",
                "-" * 40,
                "",
            ]

    lines += [
        "=" * 60,
        "Write the analyst brief now, following the required structure.",
    ]

    return "\n".join(lines)


def _fallback_report(state: AgentState, error: str) -> str:
    """
    Plain-text fallback report if the Claude API call fails.
    Ensures the graph always produces *something* rather than crashing.
    """
    lines = [
        f"# Financial Research Report — {state.get('company_target', 'Unknown')}",
        "",
        f"**Note:** Report generation encountered an error: {error}",
        "",
        "## Raw Research Data",
        "",
    ]
    for r in state.get("tool_results", []):
        status = "✓" if r["success"] else "✗"
        lines.append(f"{status} **{r['tool_name']}**: {r['output'][:300]}...")

    return "\n".join(lines)