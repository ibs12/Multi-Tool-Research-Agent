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

# See supervisor.py for the model choice + the thinking/MAX_TOKENS caveat.
MODEL      = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
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
    The financial snapshot table MUST use EXACTLY this column order
    (omit a column only when no data at all exists for it):

      Metric | FY2022 | FY2023 | FY2024 | FY2025 | Q2 FY2026 | FY2026E | FY2027E

    Column definitions and citation rules (STRICTLY separate sources — never mix in one cell):
    - FY2022 – FY2025 : Annual figures from HISTORICAL FINANCIALS tool. Cite each cell ¹.
    - Q2 FY2026       : Current-period figures from RAG_SEARCH (SEC 10-Q filing). Cite each cell ².
                        • Use the quarter's actual value (e.g. Revenue $111.2B for the quarter).
                        • For metrics reported on a half-year basis write "H1: $X" in the cell.
                        • If the figure is not in the SEC filing data, write "—" (not N/A).
                        • DO NOT move this data to a prose note below the table.
                          It MUST appear as a column. This column is the most recent primary-source data.
    - FY2026E/FY2027E : Forward estimates from CONSENSUS_ESTIMATES. Cite each cell ³.
                        Include the estimate range: e.g. "$478B (Low $468B – High $485B)".

    Rows to include — only rows where at least one cell has a real value:
      Revenue | Gross Margin % | Net Income | EPS

    IMPORTANT for banks and financial institutions (JPMorgan, Citigroup, Goldman Sachs, etc.):
      Gross Margin % is not a standard metric for banks. Substitute Net Interest Margin
      or Operating Margin if that data is available in the tool results.
      If neither is available, OMIT the Gross Margin row entirely — do not show a row
      of N/A or "—" values across all columns.

    ## Risk Factors
    3-5 bullet points. Draw from news sentiment, sector context, and academic research.

    ## Academic & Research Context
    Cite any relevant arXiv papers found. If none, omit this section.

    ## Analyst Verdict
    Bullish / Neutral / Bearish with a one-paragraph justification.
    If consensus estimates are available, reference the analyst recommendation rating and count.

    Rules:
    - NEVER invent financial figures. If data is missing, say so.
    - Cite your source for each claim: [Web Search], [Wikipedia], [ArXiv], [Calculator], [SEC Filing], [Analyst Consensus]
    - RAG_SEARCH results contain verbatim SEC filing text — always cite these as [SEC Filing]
    - CONSENSUS_ESTIMATES results: ALWAYS include the analyst count in citations.
      Example: "$8.74 FY2026E EPS [Analyst Consensus — 42 analysts, Yahoo Finance]"
    - An estimate from 3 analysts carries far less weight than one from 42 — always report the count
    - NEVER present a consensus estimate without citing the number of analysts
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
    ]

    # If the run stopped before the agent judged the research complete, tell
    # synthesis so it can temper the verdict rather than overstating confidence.
    _incomplete = {
        "iteration_budget_exhausted":
            "RESEARCH STATUS: stopped at the iteration budget before the agent "
            "signalled completion — the data below may be partial. Note this in "
            "the Analyst Verdict and temper confidence accordingly.",
        "no_new_tools":
            "RESEARCH STATUS: the agent stopped because it had no new tools left "
            "to run, not because it judged the research complete — treat coverage "
            "as partial.",
    }
    note = _incomplete.get(state.get("termination_reason"))
    if note:
        lines.append(note)

    lines += [
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
