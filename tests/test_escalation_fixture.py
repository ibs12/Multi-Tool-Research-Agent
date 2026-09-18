"""
tests/test_escalation_fixture.py
--------------------------------
The empirically-pinned escalation run (tests/fixtures/escalation_run.json) —
a REAL multi-agent run on a query with no verifiable primary-source financials
(a private company whose web evidence self-contradicts). The compliance-checker
correctly escalated rather than shipping a brief.

Deterministic, no LLM: this is both the X1 milestone proof that escalation
fires on a real query and the shape the Map 2 E4 Tier-1 gate scores against.
It guards the escalation contract (ADR-0009) against regression.

Regenerate the fixture with:
    PGVECTOR_URL= python tests/fixtures/capture_escalation.py "Analyse SpaceX investment outlook"
"""

from __future__ import annotations

import json
import os

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "escalation_run.json")

_GAP_TYPES = {"missing_disclosure", "conflicting_sources",
              "unverifiable_citation", "regulatory_flag", None}


@pytest.fixture(scope="module")
def run():
    with open(FIXTURE) as f:
        return json.load(f)


def test_run_terminated_in_escalation(run):
    # The Map 2 E4 gate scores escalation off exactly these two fields.
    assert run["termination_reason"] == "escalated"
    assert run["compliance_verdict"]["verdict"] == "escalate"


def test_escalation_package_is_interrupt_shaped(run):
    pkg = run["escalation"]
    assert pkg is not None
    assert set(pkg) >= {"reason", "unresolved", "evidence_summary", "compliance_reasons"}
    assert pkg["unresolved"], "escalation must record what a human needs to decide"
    assert isinstance(pkg["reason"], str) and pkg["reason"].strip()


def test_compliance_gave_concrete_reasons(run):
    reasons = run["compliance_verdict"]["reasons"]
    assert reasons and all(isinstance(r, str) and r.strip() for r in reasons)


def test_gap_type_is_a_known_category(run):
    # Feeds the Map 2 E1 gap_type breakdown of the false-clear/escalate metrics.
    assert run["compliance_verdict"].get("gap_type") in _GAP_TYPES


def test_the_pipeline_actually_ran_research_before_escalating(run):
    # Escalation must be a judgement over gathered evidence, not an early bail.
    assert run["tools_called"], "no tools ran — escalation would be vacuous"
    assert run["iteration_count"] >= 1
