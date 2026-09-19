"""
eval/generate_dataset.py
────────────────────────
Generate the Map 2 eval dataset from SEC XBRL ground truth (E6). Programmatic,
seeded, and versioned — the TakeMeter antidote: categories come from *detectable
structural signals*, not hand-eyeballing at volume.

Categories (E6.Q1/Q2):
  correct_extraction  — clean multi-concept annual values, all pinned to one
                        accession (frames-canonical CYxxxx values).
  ambiguous           — RESTATEMENTS: one period end with ≥2 distinct values
                        under different accessions. Scored against the
                        AS-ORIGINALLY-FILED accession (Apple FY2008-style).
  missing_conflicting — a tested concept not tagged (e.g. banks have no
                        GrossProfit ⇒ gross margin undisclosed). The system must
                        say "not disclosed", not fabricate. gap = missing_disclosure.
  should_escalate     — (a) 10-K/A amendments that actually CHANGED a tested
                        figure beyond tolerance (E6.Q2 narrowing — a bare
                        amendment is NOT a signal); (b) controlled synthetic
                        perturbation (a citation made unverifiable), flagged.

The ~18 boundary cases (E6.Q3) are hand-curated with written rationales and live
in eval/boundary_cases.jsonl — NOT generated here. Merge them in at scoring time.

Run:
    PYTHONPATH=. python eval/generate_dataset.py           # writes eval/dataset/
"""

from __future__ import annotations

import random
import re
from datetime import date, datetime, timezone
from pathlib import Path

from eval import frames_client as sec
from eval.schema import Case, write_jsonl

# ── Config ────────────────────────────────────────────────────────────────────

SEED = 20260918
DATASET_DIR = Path(__file__).resolve().parent / "dataset"
YEARS = list(range(2019, 2025))              # domestic 10-K, ≥2009 (R2); recent window

# Sector-spread basket (E6.Q1): large-cap domestic filers with clean XBRL.
BASKET: list[tuple[str, int]] = [
    ("Apple Inc.", 320193), ("Microsoft Corp.", 789019), ("NVIDIA Corp.", 1045810),
    ("Alphabet Inc.", 1652044), ("Meta Platforms Inc.", 1326801),          # tech
    ("JPMorgan Chase & Co.", 19617), ("Goldman Sachs Group Inc.", 886982),
    ("Bank of America Corp.", 70858),                                       # finance
    ("Johnson & Johnson", 200406), ("Pfizer Inc.", 78003),
    ("UnitedHealth Group Inc.", 731766),                                    # healthcare
    ("Exxon Mobil Corp.", 34088), ("Chevron Corp.", 93410),                 # energy
    ("Coca-Cola Co.", 21344), ("Procter & Gamble Co.", 80424),
    ("Walmart Inc.", 104169), ("Costco Wholesale Corp.", 909832),           # consumer
    ("Caterpillar Inc.", 18230), ("Boeing Co.", 12927), ("3M Co.", 66740),  # industrial
]

# Revenue is a union of concepts (R2): prefer the modern tag, fall back.
REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
REL_TOL = 0.005          # E2.Q1 level tolerance, reused for amendment change test


# ── Frame helpers ─────────────────────────────────────────────────────────────

def _frame_entry(entries: list[dict], frame: str) -> dict | None:
    hits = [e for e in entries if e.get("frame") == frame]
    return hits[-1] if hits else None


def _revenue_frame(facts: dict, frame: str):
    for concept in REVENUE_CONCEPTS:
        e = _frame_entry(sec.concept_entries(facts, concept), frame)
        if e:
            return e, concept
    return None, None


def _fv(entry: dict) -> dict:
    """A tested_fields spec from a frame entry."""
    return {"expected_value": entry["val"], "source_accession": entry["accn"]}


# ── Builders ──────────────────────────────────────────────────────────────────

def build_correct_extraction(company: str, cik: int, facts: dict) -> list[Case]:
    cases = []
    ni_entries  = sec.concept_entries(facts, "NetIncomeLoss")
    eps_entries = (sec.concept_entries(facts, "EarningsPerShareDiluted", "USD/shares")
                   or sec.concept_entries(facts, "EarningsPerShareBasic", "USD/shares"))
    gp_entries  = sec.concept_entries(facts, "GrossProfit")

    for year in YEARS:
        frame = f"CY{year}"
        ni = _frame_entry(ni_entries, frame)
        rev, _ = _revenue_frame(facts, frame)
        eps = _frame_entry(eps_entries, frame)
        if not (ni and rev and eps):
            continue

        tested = {"net_income": _fv(ni), "revenue": _fv(rev), "eps": _fv(eps)}
        gp = _frame_entry(gp_entries, frame)
        if gp and rev["val"]:
            margin = round(gp["val"] / rev["val"] * 100, 1)
            tested["gross_margin"] = {"expected_value": margin, "source_accession": gp["accn"]}

        cases.append(Case(
            id=f"ce-{cik}-{year}", company=company, cik=cik, accession=ni["accn"],
            period=f"FY{year}", category="correct_extraction", tested_fields=tested,
            expected_verdict="clear", gap_type="none",
            source="xbrl-frames-canonical",
            notes=f"Clean annual values for CY{year}, all accession-pinned.",
        ))
    return cases


def _concept_restatements(company: str, cik: int, entries: list[dict],
                          field_name: str) -> list[Case]:
    """Full-year periods where a concept was materially restated across accessions."""
    annual = [e for e in entries
              if e.get("fp") == "FY" and e.get("form", "").startswith("10-K") and e.get("start")]
    # Key on the FULL period (start, end) — grouping by end alone would conflate
    # a full fiscal year with a Q4 sharing the same period-end date.
    by_period: dict[tuple, list[dict]] = {}
    for e in annual:
        by_period.setdefault((e["start"], e["end"]), []).append(e)

    cases = []
    for (start, end), grp in by_period.items():
        if int(end[:4]) < 2010:                       # R2: ≥2009; recent-enough window
            continue
        span = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
        if not (350 <= span <= 380):                  # full fiscal year only
            continue
        if len({e["accn"] for e in grp}) < 2:
            continue
        original = min(grp, key=lambda e: e["accn"])  # as-originally-filed
        if not original["val"]:
            continue
        restatements = [e for e in grp
                        if abs(e["val"] - original["val"]) / abs(original["val"]) > 0.01]
        if not restatements:                          # immaterial reclassification → skip
            continue
        restated = max(restatements, key=lambda e: abs(e["val"] - original["val"]))
        cases.append(Case(
            id=f"amb-{field_name}-{cik}-{end}", company=company, cik=cik,
            accession=original["accn"], period=f"FY~{end[:4]}",
            category="ambiguous",
            tested_fields={field_name: _fv(original)},
            expected_verdict="clear", gap_type="none",
            source="xbrl-restatement",
            notes=(f"{field_name} restated: as-filed {original['val']} (accn "
                   f"{original['accn']}) vs later {restated['val']} (accn "
                   f"{restated['accn']}). Score the as-originally-filed figure."),
        ))
    return cases


def build_ambiguous_restatements(company: str, cik: int, facts: dict) -> list[Case]:
    """Material full-year restatements of net income OR revenue (E6.Q2)."""
    cases = _concept_restatements(company, cik,
                                  sec.concept_entries(facts, "NetIncomeLoss"), "net_income")
    for concept in REVENUE_CONCEPTS:
        cases += _concept_restatements(company, cik,
                                       sec.concept_entries(facts, concept), "revenue")
    # One ambiguous case per period (a period could restate both concepts).
    seen, deduped = set(), []
    for c in cases:
        if c.period not in seen:
            seen.add(c.period)
            deduped.append(c)
    return deduped


def build_missing_gross_margin(company: str, cik: int, facts: dict) -> list[Case]:
    """Filers with no GrossProfit tag ⇒ gross margin undisclosed (E6.Q2)."""
    if sec.concept_entries(facts, "GrossProfit"):
        return []                                     # margin IS disclosed → not this case
    cases = []
    ni_entries = sec.concept_entries(facts, "NetIncomeLoss")
    for year in YEARS:
        frame = f"CY{year}"
        ni = _frame_entry(ni_entries, frame)
        rev, _ = _revenue_frame(facts, frame)
        if not (ni and rev):
            continue
        cases.append(Case(
            id=f"miss-{cik}-{year}", company=company, cik=cik, accession=ni["accn"],
            period=f"FY{year}", category="missing_conflicting",
            tested_fields={
                "net_income": _fv(ni),
                "revenue": _fv(rev),
                "gross_margin": {"expected_value": None, "source_accession": ni["accn"]},
            },
            expected_verdict="clear", gap_type="missing_disclosure",
            source="xbrl-missing-concept",
            notes="No GrossProfit tag — gross margin is undisclosed; the system must "
                  "say so, not fabricate a margin.",
        ))
    return cases


def build_amendment_escalations(company: str, cik: int, facts: dict) -> list[Case]:
    """10-K/A that actually CHANGED a tested figure beyond tolerance (E6.Q2 narrowing).

    A bare amendment is NOT a signal — only a figure-changing one is."""
    cases = []
    ni_entries = sec.concept_entries(facts, "NetIncomeLoss")
    by_end: dict[str, dict] = {}
    for e in ni_entries:
        by_end.setdefault(e["end"], {})[e.get("form", "")] = e
    for end, forms in by_end.items():
        orig, amend = forms.get("10-K"), forms.get("10-K/A")
        if orig and amend and orig["val"]:
            change = abs(amend["val"] - orig["val"]) / abs(orig["val"])
            if change > REL_TOL:
                cases.append(Case(
                    id=f"esc-amend-{cik}-{end}", company=company, cik=cik,
                    accession=amend["accn"], period=f"FY~{end[:4]}",
                    category="should_escalate",
                    tested_fields={"net_income": _fv(amend)},
                    expected_verdict="escalate", gap_type="conflicting_sources",
                    source="xbrl-amendment-figure-change",
                    notes=(f"10-K/A changed net income {orig['val']}→{amend['val']} "
                           f"({change:.1%}) — a material restatement a human should review."),
                ))
    return cases


def build_synthetic_escalations(clean: list[Case], n: int, rng: random.Random) -> list[Case]:
    """Controlled perturbation (E6.Q2, flagged): break a citation's verifiability
    on an otherwise-clean case so escalation is the only defensible outcome."""
    out = []
    for base in rng.sample(clean, min(n, len(clean))):
        field = next(iter(base.tested_fields))
        tf = {field: {"expected_value": base.tested_fields[field]["expected_value"],
                      "source_accession": "0000000000-00-000000"}}   # nonexistent accn
        out.append(Case(
            id=f"esc-synth-{base.cik}-{base.period}", company=base.company, cik=base.cik,
            accession="0000000000-00-000000", period=base.period,
            category="should_escalate", tested_fields=tf,
            expected_verdict="escalate", gap_type="unverifiable_citation",
            synthetic=True, source="synthetic-perturbation",
            notes="Synthetic: the cited primary source does not exist, so the "
                  "figure is unverifiable and the run should escalate.",
        ))
    return out


# ── Orchestration ─────────────────────────────────────────────────────────────

def generate() -> dict:
    rng = random.Random(SEED)
    correct, ambiguous, missing, escalate = [], [], [], []

    for company, cik in BASKET:
        facts = sec.companyfacts(cik)
        correct.extend(build_correct_extraction(company, cik, facts))
        ambiguous.extend(build_ambiguous_restatements(company, cik, facts))
        missing.extend(build_missing_gross_margin(company, cik, facts))
        escalate.extend(build_amendment_escalations(company, cik, facts))

    # Cap each bucket to E1's target distribution (~150 total: correct-extraction
    # ~65-70, ambiguous/missing ≥25 each, should-escalate ≥30) so no signal that
    # happens to be abundant skews the set.
    for bucket in (correct, ambiguous, missing):
        rng.shuffle(bucket)
    correct   = correct[:68]
    ambiguous = ambiguous[:28]
    missing   = missing[:28]

    # Top up should-escalate to the ≥30 floor with flagged synthetic perturbation.
    need = max(0, 30 - len(escalate))
    escalate.extend(build_synthetic_escalations(correct, need, rng))

    cases = correct + ambiguous + missing + escalate
    rng.shuffle(cases)

    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    n = write_jsonl(cases, DATASET_DIR / "cases.jsonl")

    counts = {
        "correct_extraction": len(correct),
        "ambiguous": len(ambiguous),
        "missing_conflicting": len(missing),
        "should_escalate": len(escalate),
        "total": n,
    }
    manifest = {
        "dataset_version": f"e6-{date.today().isoformat()}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": SEED,
        "basket_size": len(BASKET),
        "years": [YEARS[0], YEARS[-1]],
        "ground_truth": "SEC XBRL companyfacts (accession-pinned; R2)",
        "revenue_concepts": REVENUE_CONCEPTS,
        "counts": counts,
        "note": "Boundary cases (~18, E6.Q3) are hand-curated in "
                "eval/boundary_cases.jsonl and merged at scoring time, not here.",
    }
    import json
    (DATASET_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    import json
    print(json.dumps(generate(), indent=2))
