"""
agent/nodes/synthesis.py
────────────────────────
Produces the final analyst brief from accumulated tool results.

Two entry points:
  synthesis_node(state)       — blocking, used by CLI (run.py) and batch API
  stream_synthesis(state)     — generator that yields text tokens one by one,
                                used by the streaming SSE endpoint for real-time
                                token delivery to the browser

Both share the same prompt-building logic and apply prompt caching on the
system prompt so repeated runs of the same company benefit from cache hits.

Interview talking point:
  Keeping synthesis separate from the supervisor is the "single
  responsibility" principle applied to LLM nodes: the supervisor reasons
  about *what to research*; synthesis reasons about *what to say*.
  Streaming synthesis is implemented with asyncio.Queue so the generator
  thread can push tokens to the async SSE generator without blocking.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dotenv import load_dotenv
import anthropic

from agent.state import AgentState

load_dotenv()

MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 2048


# ── System prompt ─────────────────────────────────────────────────────────────

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


# ── Blocking entry point (CLI / batch API) ────────────────────────────────────

def synthesis_node(state: AgentState) -> dict:
    """
    Calls Claude with the full research corpus to produce the final report.
    Returns a state-update dict with final_report populated.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_message = _build_synthesis_prompt(state)
    system = _build_system_prompt()

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
        report = response.content[0].text.strip()
    except anthropic.APIError as e:
        report = _fallback_report(state, error=str(e))

    return {"final_report": report, "error": None}


# ── Streaming entry point (SSE API) ───────────────────────────────────────────

def stream_synthesis(state: AgentState) -> Iterator[tuple[str, str]]:
    """
    Sync generator that yields (event, payload) tuples:
      ("chunk", text)        — one token / text delta from Claude
      ("done",  full_report) — emitted once after the last token

    Designed to run inside a ThreadPoolExecutor thread so the blocking
    Anthropic streaming call doesn't stall the asyncio event loop.
    The API layer bridges this to an async generator via asyncio.Queue.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    user_message = _build_synthesis_prompt(state)
    system = _build_system_prompt()

    chunks: list[str] = []
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            for text in stream.text_stream:
                chunks.append(text)
                yield ("chunk", text)
        yield ("done", "".join(chunks))
    except Exception as e:
        fallback = _fallback_report(state, error=str(e))
        yield ("done", fallback)


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_synthesis_prompt(state: AgentState) -> str:
    """
    Full research corpus passed verbatim to Claude.
    Synthesis needs untruncated tool output to write accurate citations.
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


# ── Fallback ──────────────────────────────────────────────────────────────────

def _fallback_report(state: AgentState, error: str) -> str:
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
