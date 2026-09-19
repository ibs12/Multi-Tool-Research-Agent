"""
agent/nodes/agents.py
─────────────────────
The two specialized agents that turn the single research loop into a
multi-agent pipeline (ADR-0006 peer handoff, ADR-0007 roster):

    research  →  risk_analyst  →  compliance_checker  →  {clear | escalate}
                     │ hand-back        │ hand-back
                     ▼                  ▼
                  research           research

Each agent is a first-class node returning ``Command(goto=…)`` — it owns its
own next-hop decision. Both use the Anthropic native tool-use API for a
*structured* verdict (no string parsing), mirroring the supervisor.

Termination is guaranteed: hand-backs to research are capped at MAX_HANDBACKS
(tracked in ``handoff.revision_count``); once the cap is hit, risk proceeds and
compliance must decide clear-or-escalate rather than ask for more evidence.

Fail-safe on the agents' own errors: a risk-analyst API error proceeds (risk is
advisory); a compliance API error **escalates** — never silently ship a brief
whose defensibility could not be checked (ADR-0009 semantics).
"""

from __future__ import annotations

import os
from dotenv import load_dotenv
import anthropic
from langgraph.graph import END
from langgraph.types import Command

from agent.state import AgentState

load_dotenv()

# Same flagship model + no-thinking caveat as the supervisor (see supervisor.py).
MODEL      = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")
MAX_TOKENS = 1024

# Cap hand-backs to research so the pipeline always terminates.
MAX_HANDBACKS = 1

RISK_ANALYST_ERROR = "[Risk Analyst Error]"
COMPLIANCE_ERROR   = "[Compliance Error]"


# ── Tool schemas (structured verdicts) ────────────────────────────────────────

ASSESS_RISK_SCHEMA = {
    "name": "assess_risk",
    "description": "Record your investment-risk read of the evidence gathered so far.",
    "input_schema": {
        "type": "object",
        "properties": {
            "risk_summary": {
                "type": "string",
                "description": "2-4 sentences: the investment-risk picture from the evidence.",
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific red flags: valuation anomalies, negative EPS/FCF pivots, "
                               "sector risk, governance/legal exposure. Empty list if none.",
            },
            "evidence_sufficient": {
                "type": "boolean",
                "description": "False ONLY if the evidence is too thin to assess risk and research "
                               "should gather more; True if you can render a risk read now.",
            },
        },
        "required": ["risk_summary", "red_flags", "evidence_sufficient"],
    },
}

COMPLIANCE_REVIEW_SCHEMA = {
    "name": "compliance_review",
    "description": "Record your defensibility verdict on whether a brief may ship.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["clear", "needs-revision", "escalate"],
                "description": "clear = defensible, ship the brief; needs-revision = fixable gap, "
                               "send back to research; escalate = not defensibly answerable, a "
                               "human must decide.",
            },
            "reasons": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete reasons: uncited figures, fabricated numbers, insufficient "
                               "analyst counts, conflicting/missing evidence, regulatory red flags.",
            },
            "gap_type": {
                "type": "string",
                "enum": ["missing_disclosure", "conflicting_sources",
                         "unverifiable_citation", "regulatory_flag", "none"],
                "description": "The dominant gap category (Map 2 E1). 'none' when clear.",
            },
            "escalation_summary": {
                "type": "string",
                "description": "When escalating: one-glance summary of what a human must decide. "
                               "Empty otherwise.",
            },
        },
        "required": ["verdict", "reasons", "gap_type"],
    },
}


# ── Shared helpers ────────────────────────────────────────────────────────────

def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _evidence_digest(state: AgentState, limit: int = 2500) -> str:
    """Digest of the research corpus for an agent to reason over.

    The per-tool cap is generous (not 900) because the compliance-checker must
    actually *verify figures* — too aggressive a truncation cut off e.g. a
    consensus 2027 estimate and made compliance escalate on a truncation
    artifact rather than a genuine gap (observed in a live run). Still bounded
    so several tools' output stays within a sensible prompt size."""
    lines = [
        f"RESEARCH QUERY: {state.get('query', '')}",
        f"COMPANY TARGET: {state.get('company_target', 'Unknown')}",
        "",
        "EVIDENCE GATHERED:",
    ]
    results = state.get("tool_results", [])
    if not results:
        lines.append("  (no tool results)")
    for r in results:
        status = "OK" if r.get("success") else "FAILED"
        out = r.get("output", "")
        preview = out[:limit] + ("…" if len(out) > limit else "")
        lines += [f"  [{status}] {r.get('tool_name','?').upper()}:", f"  {preview}", ""]
    return "\n".join(lines)


def _tool_block(response) -> dict:
    """Pull the first tool_use block's input dict from a response, or {}."""
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    return {}


def _revision_count(state: AgentState) -> int:
    return int(state.get("handoff", {}).get("revision_count", 0))


# ── Risk-analyst agent (ADR-0007) ─────────────────────────────────────────────

RISK_SYSTEM = """You are an investment risk analyst. From the evidence gathered by
the research agent, produce a concise investment-risk read: red flags, valuation
anomalies, sector risk, negative earnings/cash-flow signals. You do not gather
new evidence yourself — if the evidence is genuinely too thin to assess, say so
and it will be sent back to research. Always call assess_risk."""


def risk_analyst_node(state: AgentState) -> Command:
    """Reads evidence → risk assessment → hands off to compliance (or back to
    research once, if evidence is thin)."""
    revisions = _revision_count(state)
    try:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=RISK_SYSTEM,
            tools=[ASSESS_RISK_SCHEMA],
            tool_choice={"type": "tool", "name": "assess_risk"},
            messages=[{"role": "user", "content": _evidence_digest(state)}],
        )
        plan = _tool_block(resp)
    except anthropic.APIError as e:
        # Advisory agent: note the error and proceed to compliance anyway.
        assessment = {"risk_summary": f"{RISK_ANALYST_ERROR} {e}",
                      "red_flags": [], "evidence_sufficient": True, "error": str(e)}
        return Command(goto="compliance_checker",
                       update={"handoff": {"risk_assessment": assessment}})

    assessment = {
        "risk_summary": plan.get("risk_summary", ""),
        "red_flags": plan.get("red_flags", []),
        "evidence_sufficient": bool(plan.get("evidence_sufficient", True)),
    }

    # Hand back to research once if the evidence is thin and we haven't already.
    if not assessment["evidence_sufficient"] and revisions < MAX_HANDBACKS:
        return Command(
            goto="supervisor",
            update={
                "handoff": {"risk_assessment": assessment,
                            "revision_count": revisions + 1},
                "tools_remaining": [],   # let the supervisor re-plan from scratch
            },
        )

    return Command(goto="compliance_checker",
                   update={"handoff": {"risk_assessment": assessment}})


# ── Compliance-checker agent (ADR-0007, ADR-0009) ─────────────────────────────

COMPLIANCE_SYSTEM = """You are a compliance checker with authority to block or escalate.
Judge whether a defensible analyst brief can ship from this evidence.

Choose the verdict by SEVERITY, not perfection:
- clear: the core figures are traceable to sources and internally consistent.
  Minor gaps — a missing analyst count, an unavailable prior-year line, partial
  coverage — are acceptable: the brief ships and simply caveats them. A
  well-known public company with SEC filings and consistent figures should clear.
- needs-revision: a specific, FIXABLE gap that more research could close.
- escalate: reserved for when a defensible brief genuinely CANNOT be produced —
  the core requested figures are unverifiable against any primary source, the
  sources conflict irreconcilably, or there is a regulatory red flag. Escalate is
  a strong action; do NOT escalate merely because coverage is incomplete or a few
  figures lack a citation.

No fabricated numbers; analyst-consensus claims should carry an analyst count.
Always call compliance_review."""


def compliance_checker_node(state: AgentState) -> Command:
    """Terminal gate: clear → brief (synthesis, outside the graph), needs-revision
    → hand back to research (capped), escalate → terminal escalation (ADR-0009)."""
    revisions = _revision_count(state)
    risk = state.get("handoff", {}).get("risk_assessment", {})
    digest = _evidence_digest(state) + f"\n\nRISK ANALYST READ:\n{risk}\n"

    try:
        resp = _client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=COMPLIANCE_SYSTEM,
            tools=[COMPLIANCE_REVIEW_SCHEMA],
            tool_choice={"type": "tool", "name": "compliance_review"},
            messages=[{"role": "user", "content": digest}],
        )
        review = _tool_block(resp)
    except anthropic.APIError as e:
        # Fail-safe: cannot verify defensibility ⇒ escalate, never auto-ship.
        return _escalate(
            state,
            verdict={"verdict": "escalate",
                     "reasons": [f"{COMPLIANCE_ERROR} {e}"], "gap_type": "regulatory_flag"},
            summary=f"Compliance check could not run ({e}); escalating rather than shipping unchecked.",
        )

    verdict = {
        "verdict": review.get("verdict", "escalate"),
        "reasons": review.get("reasons", []),
        "gap_type": review.get("gap_type") or None,
    }

    # needs-revision below the cap → hand back to research once to fill the gap.
    if verdict["verdict"] == "needs-revision" and revisions < MAX_HANDBACKS:
        return Command(
            goto="supervisor",
            update={
                "handoff": {"compliance_verdict": verdict, "revision_count": revisions + 1},
                "tools_remaining": [],
            },
        )

    # Only an EXPLICIT escalate verdict escalates (genuinely not answerable).
    if verdict["verdict"] == "escalate":
        return _escalate(state, verdict,
                         summary=review.get("escalation_summary", "")
                         or "; ".join(verdict["reasons"]) or "Unresolved compliance concerns.")

    # clear — OR a needs-revision that couldn't be resolved within the cap. A
    # needs-revision gap is fixable/minor by definition, so an unfilled one ships
    # a CAVEATED brief rather than escalating. Escalating unresolved minor gaps
    # was the E5 over-escalation finding (4/5 clean large-caps wrongly escalated);
    # escalation is reserved for the explicit verdict.
    return Command(goto=END,
                   update={"handoff": {"compliance_verdict": verdict},
                           "termination_reason": "completed"})


def _escalate(state: AgentState, verdict: dict, summary: str) -> Command:
    """Build the interrupt-shaped escalation package and route to the terminal
    escalated state (ADR-0009)."""
    package = {
        "reason": summary,
        "unresolved": verdict.get("reasons", []),
        "evidence_summary": _evidence_digest(state, limit=300),
        "research_findings": state.get("handoff", {}).get("research_findings"),
        "risk_assessment": state.get("handoff", {}).get("risk_assessment"),
        "compliance_reasons": verdict.get("reasons", []),
    }
    return Command(
        goto=END,
        update={
            "handoff": {"compliance_verdict": verdict, "escalation": package},
            "termination_reason": "escalated",
        },
    )
