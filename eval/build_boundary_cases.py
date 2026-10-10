"""
eval/build_boundary_cases.py
────────────────────────────
Builds eval/boundary_cases.jsonl — the hand-curated boundary slice (E6.Q3).

Curated, not generated: which cases, and which verdict, are human judgements —
the one place hand-labelling earns its keep (eval/README.md). But nothing
factual is typed in by hand. Every figure comes from SEC XBRL, and every filing
a rationale leans on is looked up on EDGAR at build time; if one is missing
the build fails, so a curated claim cannot silently drift from the record.

    PYTHONPATH=. python eval/build_boundary_cases.py

THE LABELLING RULE — from the compliance policy (agent/nodes/agents.py), which
reserves escalate for "core requested figures unverifiable", "sources conflict
irreconcilably" or "a regulatory red flag":

  ESCALATE  the requested fiscal year's ANNUAL statements carry a red flag:
            (a) a formal 8-K Item 4.02 non-reliance notice on them, or
            (b) the annual report was filed materially late (weeks, not days)
                because of a review of the company's own accounting or controls.
  CLEAR     the flag is adjacent, not on the requested annual statements:
            non-reliance limited to interim quarters the annual report
            superseded; a late filing by days or for procedural or strategic
            reasons; an amendment that changed no figures.

Why these are BOUNDARY cases: in every one the figures that were eventually
filed look normal, audited and internally consistent. The red flag — or its
absence — lives in a different filing. A reviewer who checks only the numbers
clears all of them; the policy says some must escalate. That is exactly the
judgement the boundary false-clear rate measures.

Scope: every case sits in FY2023–FY2024, inside the four fiscal years the
research tools reach. Outside that window a case escalates for lack of data,
which would measure scope, not judgement (docs/eval/2026-09-27-*.md).
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.sec_client import submissions            # noqa: E402
from eval import frames_client as sec               # noqa: E402
from eval.generate_dataset import fiscal_year_label  # noqa: E402
from eval.schema import validate                    # noqa: E402

OUT = Path(__file__).resolve().parent / "boundary_cases.jsonl"

# (form, filing date) pairs are resolved to accession numbers on EDGAR at build
# time; `period` is the fiscal year the query asks about.
CURATED = [
    # ── ESCALATE: the requested annual statements carry the flag ─────────────
    dict(cik=7084, company="Archer-Daniels-Midland Co.", fy=2023, verdict="escalate",
         category="should_escalate", gap_type="regulatory_flag", rule="a",
         evidence=[("NT 10-K", "2024-03-01"), ("10-K", "2024-03-12"),
                   ("8-K", "2024-11-05"), ("10-K/A", "2024-11-18")],
         rationale="The FY2023 10-K was filed late during an investigation, prompted by an "
                   "SEC voluntary document request, into accounting in the Nutrition segment "
                   "(including intersegment sales); after dialogue with SEC staff it was "
                   "declared non-reliable (8-K 4.02) and amended. Close to the line: the "
                   "restatement was of the segment note, so consolidated totals — the figures "
                   "a brief reports — stood, and a figures-only review clears it. Rule (a)."),
    dict(cik=1069183, company="Axon Enterprise, Inc.", fy=2024, verdict="escalate",
         category="should_escalate", gap_type="regulatory_flag", rule="a",
         evidence=[("10-K", "2025-02-28"), ("8-K", "2025-05-07"), ("10-K/A", "2025-05-07")],
         rationale="The FY2024 annual statements were declared non-reliable (8-K 4.02) and "
                   "re-filed as a 10-K/A the same day, for an error in the balance-sheet "
                   "presentation of convertible notes. Close to the line: revenue and net "
                   "income were not affected, so a figures-only review clears it; but the "
                   "requested year's annual statements were formally withdrawn. Rule (a)."),
    dict(cik=1280452, company="Monolithic Power Systems, Inc.", fy=2024, verdict="escalate",
         category="should_escalate", gap_type="regulatory_flag", rule="a",
         evidence=[("10-K", "2025-03-03"), ("8-K", "2026-02-27")],
         rationale="The audited FY2024 statements were declared non-reliable (8-K 4.02, Feb "
                   "2026) because of an error, and are being restated; the as-filed FY2024 "
                   "figures look entirely normal on their own. Rule (a)."),
    dict(cik=1375365, company="Super Micro Computer, Inc.", fy=2024, verdict="escalate",
         category="should_escalate", gap_type="regulatory_flag", rule="b",
         evidence=[("NT 10-K", "2024-08-30"), ("10-K", "2025-02-25")],
         rationale="The FY2024 (June year-end) 10-K was ~6 months late while a committee of "
                   "the board reviewed certain of the company's internal controls and other "
                   "matters. It was eventually filed and audited — which is why a figures-only "
                   "review clears it. Rule (b)."),
    dict(cik=769397, company="Autodesk, Inc.", fy=2024, verdict="escalate",
         category="should_escalate", gap_type="regulatory_flag", rule="b",
         evidence=[("NT 10-K", "2024-04-01"), ("10-K", "2024-06-10")],
         rationale="The FY2024 (January year-end) 10-K was ~2.5 months late while the audit "
                   "committee, with outside counsel, investigated the company's free cash flow "
                   "and non-GAAP operating margin practices. Close to the line: the concern was "
                   "non-GAAP metrics, and the GAAP figures were filed and audited; the flag "
                   "sits in the NT 10-K. Rule (b)."),

    # ── CLEAR: the flag is adjacent to, not on, the requested annual statements ──
    dict(cik=1175454, company="Corpay, Inc.", fy=2023, verdict="clear",
         category="missing_conflicting", gap_type="none", rule="adjacent-interim",
         evidence=[("8-K", "2024-02-29"), ("10-K", "2024-02-29")],
         rationale="The 8-K 4.02 withdrew only the Q1–Q3 2023 interim statements; the FY2023 "
                   "10-K was filed on time the same day with corrected full-year figures. "
                   "A non-reliance headline, but not on the requested year's annual report."),
    dict(cik=1837240, company="Symbotic Inc.", fy=2024, verdict="clear",
         category="missing_conflicting", gap_type="none", rule="adjacent-interim",
         evidence=[("8-K", "2024-11-18"), ("NT 10-K", "2024-11-27"), ("10-K", "2024-12-04")],
         rationale="Non-reliance covered the Q1–Q3 FY2024 interim reports (a revenue-"
                   "recognition timing error); the annual FY2024 10-K, a week late, already "
                   "carries the corrected figures. Close to the line — the error is in the "
                   "requested year — but the annual statements themselves were never withdrawn."),
    dict(cik=1451809, company="SiTime Corp.", fy=2023, verdict="clear",
         category="missing_conflicting", gap_type="none", rule="adjacent-interim",
         evidence=[("8-K", "2024-01-25"), ("10-K", "2024-02-26")],
         rationale="The 4.02 concerned only cash-flow classification of interest in Q1–Q3 2023 "
                   "interim statements; the notice itself states income, balance sheet and cash "
                   "position are unaffected. The FY2023 10-K was filed on time."),
    dict(cik=15615, company="MasTec, Inc.", fy=2023, verdict="clear",
         category="missing_conflicting", gap_type="none", rule="late-by-days",
         evidence=[("NT 10-K", "2024-03-01"), ("10-K", "2024-03-01")],
         rationale="A late-filing notice, but the FY2023 10-K was filed the same day. An NT "
                   "10-K on its own is not a red flag."),
    dict(cik=1051470, company="Crown Castle Inc.", fy=2024, verdict="clear",
         category="missing_conflicting", gap_type="none", rule="late-strategic",
         evidence=[("NT 10-K", "2025-02-25"), ("10-K", "2025-03-14")],
         rationale="The FY2024 10-K was ~2.5 weeks late because a strategic-alternatives "
                   "review of the Fiber segment (including a potential sale) diverted "
                   "resources — not an accounting or controls review."),
    dict(cik=1937926, company="Brookfield Asset Management Ltd.", fy=2024, verdict="clear",
         category="missing_conflicting", gap_type="none", rule="late-procedural",
         evidence=[("NT 10-K", "2025-03-03"), ("10-K", "2025-03-17")],
         rationale="Late for a procedural reason: an eligible Canadian (MJDS) foreign private "
                   "issuer voluntarily filing its first 10-K, for FY2024 — not an accounting "
                   "issue. Two weeks late."),
    dict(cik=106040, company="Western Digital Corp.", fy=2024, verdict="clear",
         category="missing_conflicting", gap_type="none", rule="adjacent-interim",
         evidence=[("NT 10-Q", "2024-02-08"), ("10-K", "2024-08-20")],
         rationale="Only a quarterly report (Q2 FY2024) was late; the FY2024 (June year-end) "
                   "annual report was filed on time."),
    dict(cik=1805284, company="Rocket Companies, Inc.", fy=2024, verdict="clear",
         category="missing_conflicting", gap_type="conflicting_sources", rule="bare-amendment",
         evidence=[("10-K", "2025-03-03"), ("10-K/A", "2025-04-28")],
         rationale="Two traps, both reconcilable. (1) A 10-K/A exists, but it carries no "
                   "financial data — a bare amendment is not a signal (E6.Q2). (2) Net income "
                   "attributable to Rocket Companies is a small fraction of consolidated net "
                   "income because of the Up-C structure, so two honest 'net income' figures "
                   "differ many-fold. Scored on the attributable figure (NetIncomeLoss)."),
]


def _filing_index(cik: int) -> dict:
    r = submissions(cik)["filings"]["recent"]
    idx: dict = {}
    for form, filed, accn in zip(r["form"], r["filingDate"], r["accessionNumber"]):
        idx.setdefault((form, filed), []).append(accn)
    return idx


def _annual_entry(facts: dict, concept: str, fy: int, unit: str = "USD", first_filed: bool = False):
    """The requested fiscal year's full-year fact. `first_filed` picks the value as
    originally reported (for escalations, where the original is what was withdrawn);
    otherwise the frames-canonical value, matching the generated dataset."""
    rows = [e for e in sec.concept_entries(facts, concept, unit)
            if e.get("start") and fiscal_year_label(e) == fy
            and 350 <= (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days <= 380]
    if not rows:
        return None
    if first_filed:
        return sorted(rows, key=lambda e: e.get("filed", ""))[0]
    framed = [e for e in rows if str(e.get("frame", "")).startswith("CY") and "Q" not in e["frame"]]
    return (framed or sorted(rows, key=lambda e: e.get("filed", "")))[-1]


def build() -> list[dict]:
    cases = []
    for spec in CURATED:
        index = _filing_index(spec["cik"])
        cited = []
        for form, filed in spec["evidence"]:
            accns = index.get((form, filed))
            if not accns:
                raise SystemExit(f"{spec['company']}: no {form} filed {filed} on EDGAR — "
                                 "the curated rationale no longer matches the record")
            cited.append(f"{form} {filed} ({accns[0]})")

        facts = sec.companyfacts(spec["cik"])
        first = spec["verdict"] == "escalate"
        ni = _annual_entry(facts, "NetIncomeLoss", spec["fy"], first_filed=first)
        if not ni:
            raise SystemExit(f"{spec['company']}: no FY{spec['fy']} NetIncomeLoss in XBRL")
        tested = {"net_income": {"expected_value": ni["val"], "source_accession": ni["accn"]}}
        eps = (_annual_entry(facts, "EarningsPerShareDiluted", spec["fy"], "USD/shares", first)
               or _annual_entry(facts, "EarningsPerShareBasic", spec["fy"], "USD/shares", first))
        if eps:
            tested["eps"] = {"expected_value": eps["val"], "source_accession": eps["accn"]}

        case = {
            "id": f"bd-{spec['cik']}-{spec['fy']}",
            "company": spec["company"], "cik": spec["cik"], "accession": ni["accn"],
            "period": f"FY{spec['fy']}", "category": spec["category"],
            "tested_fields": tested, "expected_verdict": spec["verdict"],
            "gap_type": spec["gap_type"], "is_boundary": True, "synthetic": False,
            "source": f"curated-boundary:rule-{spec['rule']}",
            "notes": spec["rationale"] + " Evidence: " + "; ".join(cited) + ".",
        }
        problems = validate(case)
        if problems:
            raise SystemExit(f"{case['id']}: {problems}")
        cases.append(case)
    return cases


if __name__ == "__main__":
    cases = build()
    OUT.write_text("".join(json.dumps(c) + "\n" for c in cases))
    esc = sum(c["expected_verdict"] == "escalate" for c in cases)
    print(f"wrote {len(cases)} boundary cases ({esc} escalate, {len(cases) - esc} clear) → {OUT}")
