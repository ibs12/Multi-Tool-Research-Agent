"""
tests/test_handoff.py
---------------------
The multi-agent milestone (Map 1): research → risk_analyst → compliance_checker
peer handoffs (ADR-0006), the terminal clear/escalate outcomes (ADR-0009), and
the single-agent ablation (E5). Claude is mocked so the whole pipeline runs
offline with no network or keys — the same discipline as test_supervisor.py.

Run with:
    pytest tests/test_handoff.py -v
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from agent.graph import build_graph
from agent.state import make_initial_state

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


# ── A fake Anthropic client that routes by which tool the caller requested ─────

class _Block:
    def __init__(self, name, inp):
        self.type, self.name, self.input = "tool_use", name, inp


class _Resp:
    def __init__(self, blocks):
        self.content = blocks


class _Messages:
    def __init__(self, risk, compliance):
        self._risk, self._compliance = risk, compliance

    def create(self, **kw):
        names = {t["name"] for t in kw.get("tools", [])}
        if "plan_research" in names:          # supervisor: finish research at once
            return _Resp([_Block("plan_research", {
                "company_target": "Apple Inc. (AAPL)",
                "reasoning": "enough gathered",
                "tools_to_call": [],
                "ready_to_synthesise": True,
            })])
        if "assess_risk" in names:
            return _Resp([_Block("assess_risk", self._risk)])
        if "compliance_review" in names:
            return _Resp([_Block("compliance_review", self._compliance)])
        return _Resp([])


def _fake_anthropic(risk, compliance):
    class _Fake:
        def __init__(self, *a, **k):
            self.messages = _Messages(risk, compliance)
    return _Fake


def _run(risk, compliance, agent_mode="multi"):
    graph = build_graph()
    with patch("anthropic.Anthropic", _fake_anthropic(risk, compliance)):
        state = make_initial_state("Analyse Apple Inc.", agent_mode=agent_mode)
        return graph.invoke(state)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_clear_path_runs_full_pipeline_to_completed():
    out = _run(
        risk={"risk_summary": "moderate", "red_flags": [], "evidence_sufficient": True},
        compliance={"verdict": "clear", "reasons": [], "gap_type": "none"},
    )
    assert out["termination_reason"] == "completed"
    # both agents wrote their handoff contract fields
    assert out["handoff"]["risk_assessment"]["risk_summary"] == "moderate"
    assert out["handoff"]["compliance_verdict"]["verdict"] == "clear"
    assert "escalation" not in out["handoff"]


def test_escalate_path_ends_terminal_with_package():
    out = _run(
        risk={"risk_summary": "severe", "red_flags": ["negative FCF"], "evidence_sufficient": True},
        compliance={"verdict": "escalate",
                    "reasons": ["uncited revenue figure", "conflicting sources"],
                    "gap_type": "unverifiable_citation",
                    "escalation_summary": "a human must reconcile the revenue figures"},
    )
    assert out["termination_reason"] == "escalated"
    pkg = out["handoff"]["escalation"]
    # interrupt-shaped payload (ADR-0009)
    assert set(pkg) >= {"reason", "unresolved", "evidence_summary", "compliance_reasons"}
    assert "reconcile" in pkg["reason"]
    # no brief was produced on the escalate path
    assert not out.get("final_report")


def test_single_agent_mode_skips_the_specialized_agents():
    # In single mode the research loop exits straight to END — no risk/compliance.
    out = _run(
        risk={"risk_summary": "unused", "red_flags": [], "evidence_sufficient": True},
        compliance={"verdict": "clear", "reasons": [], "gap_type": "none"},
        agent_mode="single",
    )
    assert out["handoff"] == {}                       # neither agent ran
    assert out["termination_reason"] == "completed"   # set by the supervisor


def test_needs_revision_hands_back_then_escalates_at_the_cap():
    # Compliance always says needs-revision; the hand-back cap forces a terminal
    # escalate rather than looping forever (MAX_HANDBACKS).
    out = _run(
        risk={"risk_summary": "ok", "red_flags": [], "evidence_sufficient": True},
        compliance={"verdict": "needs-revision", "reasons": ["thin evidence"],
                    "gap_type": "missing_disclosure"},
    )
    assert out["termination_reason"] == "escalated"
    assert out["handoff"]["escalation"]["compliance_reasons"] == ["thin evidence"]
