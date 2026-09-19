"""
eval/schema.py
──────────────
The Map 2 eval **case** — the label schema decided in E1, plus JSONL IO and a
validator. One case = one scoreable query with machine-generated ground truth.

Schema (E1):
  id                unique slug
  company, cik      the filer
  accession         PINNED (restatements make (company, period) non-unique — the
                    value is scored as-reported-in-THIS accession, R2/E6.Q2)
  period            fiscal period label, e.g. "FY2023"
  category          one of the four buckets (coverage + minimum counts)
  tested_fields     {field: {expected_value, source_accession}} — the subset of
                    {revenue, eps, gross_margin, net_income, forward_estimate}
                    this case exercises; forward_estimate carries a consensus
                    source + as-of date instead of an accession
  expected_verdict  "clear" | "escalate" — assigned PER CASE, not derived from
                    the bucket (E1): boundary cases carry the marginal verdict
  gap_type          the dominant gap category, or "none" (feeds the E3 breakdown)
  is_boundary       True for the ~1/3 reserved marginal cases (E1/E3)
  synthetic         True when built by controlled perturbation (E6.Q2)
  source            provenance string (how this case was sourced) — E6.Q4
  notes             free text (rationale, esp. for boundary cases)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CATEGORIES = {"correct_extraction", "missing_conflicting", "ambiguous", "should_escalate"}
VERDICTS = {"clear", "escalate"}
GAP_TYPES = {"missing_disclosure", "conflicting_sources",
             "unverifiable_citation", "regulatory_flag", "none"}
FIELDS = {"revenue", "eps", "gross_margin", "net_income", "forward_estimate"}


@dataclass
class Case:
    id: str
    company: str
    cik: int
    accession: str
    period: str
    category: str
    tested_fields: dict          # {field: {"expected_value": ..., "source_accession": ...}}
    expected_verdict: str
    gap_type: str = "none"
    is_boundary: bool = False
    synthetic: bool = False
    source: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def validate(case: dict) -> list[str]:
    """Return a list of problems with a case dict; empty = valid."""
    problems: list[str] = []
    for req in ("id", "company", "cik", "accession", "period", "category",
                "tested_fields", "expected_verdict"):
        if req not in case or case[req] in (None, ""):
            problems.append(f"missing/empty field: {req}")
    if case.get("category") not in CATEGORIES:
        problems.append(f"bad category: {case.get('category')}")
    if case.get("expected_verdict") not in VERDICTS:
        problems.append(f"bad expected_verdict: {case.get('expected_verdict')}")
    if case.get("gap_type", "none") not in GAP_TYPES:
        problems.append(f"bad gap_type: {case.get('gap_type')}")

    tf = case.get("tested_fields") or {}
    if not tf:
        problems.append("tested_fields is empty")
    for name, spec in tf.items():
        if name not in FIELDS:
            problems.append(f"unknown tested field: {name}")
        if not isinstance(spec, dict) or "expected_value" not in spec:
            problems.append(f"field {name} missing expected_value")
        elif "source_accession" not in spec:
            problems.append(f"field {name} missing source_accession")
    return problems


def write_jsonl(cases, path: str | Path) -> int:
    """Write cases (Case or dict) to JSONL; returns the count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w") as f:
        for c in cases:
            d = c.to_dict() if isinstance(c, Case) else c
            f.write(json.dumps(d) + "\n")
            n += 1
    return n


def read_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open() as f:
        return [json.loads(line) for line in f if line.strip()]
