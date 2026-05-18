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

def _build_system_prompt() -> str:
    from datetime import date
    today = date.today().strftime("%B %d, %Y")
    return f"""You are a senior financial analyst at a global investment bank. Today's date is {today}.

    When writing the report, only reference events, earnings, and data 
    that would be available as of {today}. Do not reference future quarters 
    as if they are upcoming when they may have already occurred.

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
    Primary source: use RAG_SEARCH tool results (labelled [SEC Filing]) first —
    these are verbatim passages from 10-K/10-Q filings and are the most reliable.
    Supplement with calculator results and web search figures where SEC data is absent.
    Present as a table where possible: revenue, gross margin, net income, EPS, P/E, D/E.
    If no figures were found, state that explicitly — do not fabricate numbers.

    ## Risk Factors
    3-5 bullet points. Draw from news sentiment, sector context, and academic research.

    ## Academic & Research Context
    Cite any relevant arXiv papers found. If none, omit this section.

    ## Analyst Verdict
    Bullish / Neutral / Bearish with a one-paragraph justification.

    Rules:
    - NEVER invent financial figures. If data is missing, say so.
    - Cite your source for each claim: [Web Search], [Wikipedia], [ArXiv], [Calculator], [SEC Filing]
    - RAG_SEARCH results contain verbatim SEC filing text — always cite these as [SEC Filing]
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
            system=_build_system_prompt(),
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