"""
tests/test_eval_dataset.py
--------------------------
Validates the committed Map 2 eval dataset (eval/dataset/cases.jsonl) — the
generated ground truth from E6. Deterministic, no network: reads the committed
JSONL and manifest and asserts schema conformance, the E1 category floors, and
the label invariants. Guards the dataset against a bad regeneration.

Regenerate the dataset with:
    PYTHONPATH=. python eval/generate_dataset.py
"""

from __future__ import annotations

import json
import os

import pytest

from eval.schema import validate

_ROOT = os.path.dirname(os.path.dirname(__file__))
CASES = os.path.join(_ROOT, "eval", "dataset", "cases.jsonl")
MANIFEST = os.path.join(_ROOT, "eval", "dataset", "manifest.json")

pytestmark = pytest.mark.skipif(
    not os.path.exists(CASES),
    reason="eval dataset not generated (run eval/generate_dataset.py)",
)


@pytest.fixture(scope="module")
def cases():
    with open(CASES) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_every_case_is_schema_valid(cases):
    bad = [(c.get("id"), validate(c)) for c in cases if validate(c)]
    assert not bad, f"schema-invalid cases: {bad[:5]}"


def test_category_floors_met(cases):
    # E1 minimum per-category counts.
    from collections import Counter
    n = Counter(c["category"] for c in cases)
    assert n["should_escalate"] >= 30, n
    assert n["missing_conflicting"] >= 25, n
    assert n["ambiguous"] >= 25, n
    assert 60 <= n["correct_extraction"] <= 75, n
    assert sum(n.values()) >= 140, n


def test_verdicts_align_with_bucket_defaults_for_generated_cases(cases):
    # Generated (non-boundary) cases carry their bucket-default verdict; the
    # per-case boundary exceptions (E1) live in the hand-curated file, not here.
    for c in cases:
        if c.get("is_boundary"):
            continue
        if c["category"] == "should_escalate":
            assert c["expected_verdict"] == "escalate", c["id"]
        else:
            assert c["expected_verdict"] == "clear", c["id"]


def test_every_case_pins_an_accession_and_a_source_per_field(cases):
    for c in cases:
        assert c["accession"], c["id"]
        for field, spec in c["tested_fields"].items():
            assert "source_accession" in spec, (c["id"], field)


def test_should_escalate_reasons_are_categorised(cases):
    # should_escalate cases carry a real gap_type (feeds the E3 breakdown).
    for c in cases:
        if c["category"] == "should_escalate":
            assert c["gap_type"] in {"conflicting_sources", "unverifiable_citation",
                                     "missing_disclosure", "regulatory_flag"}, c["id"]


def test_manifest_matches_dataset(cases):
    with open(MANIFEST) as f:
        manifest = json.load(f)
    assert manifest["counts"]["total"] == len(cases)
    assert manifest["dataset_version"]
    assert "SEC XBRL" in manifest["ground_truth"]
