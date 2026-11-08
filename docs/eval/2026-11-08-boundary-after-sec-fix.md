# Boundary slice after the SEC red-flag fix — 2026-11-08

**What ran.** All 13 boundary cases (`eval/boundary_cases.jsonl`), `multi` arm,
`claude-sonnet-4-6`, dataset `e6-2026-09-27`, on `main` after #64 (the SEC tool
now surfaces 8-K Items 4.02/4.01/3.01, NT 10-K/10-Q and 10-K/A). The compliance
prompt was unchanged. 13/13 finished, no errors, no tool failures. The five
earlier results (pre-#64) are kept in `eval/runs/multi.boundary-pre-sec-fix.checkpoint.jsonl`.

    PGVECTOR_URL= python eval/run_full.py --arm multi --boundary-only

Raw scorer output: [`2026-11-08-boundary-after-sec-fix.report.json`](2026-11-08-boundary-after-sec-fix.report.json).

## Headline

| | Before #64 (2026-10-10) | After #64 |
|---|---|---|
| **Boundary false-clear rate** (should escalate, cleared) | **5/5 = 100%** | **0/5 = 0%** |
| False-escalate rate (should clear, escalated) | not measured (credit ran out) | **4/8 = 50%** |
| Fabricated figures | 0 | 0 |

Making the red flags visible fixed the safety failure completely — every
should-escalate case now escalates, each citing the specific filing. The cost is
over-escalation on the clear side, and the four misses have two different causes.

## Per case

| Case | Expected | Outcome | What compliance cited |
|---|---|---|---|
| ADM FY2023 | escalate | ✅ escalated | the 8-K 4.02 on the FY2023 statements |
| Axon FY2024 | escalate | ✅ escalated | the 8-K 4.02 and the 10-K/A |
| Monolithic Power FY2024 | escalate | ✅ escalated | the 8-K 4.02, plus an EPS conflict between sources |
| Super Micro FY2024 | escalate | ✅ escalated | delisting notices and two auditor changes |
| Autodesk FY2024 | escalate | ✅ escalated | the NT 10-K for exactly FY2024 |
| Corpay FY2023 | clear | ❌ escalated | an 8-K 4.02 — which covered only Q1–Q3 2023 interims |
| Symbotic FY2024 | clear | ❌ escalated | an 8-K 4.02 — which covered only Q1–Q3 FY2024 interims |
| SiTime FY2023 | clear | ⚠️ escalated | the 4.02 (interim cash-flow classification) **and an auditor change, Jun 2024** |
| MasTec FY2023 | clear | ⚠️ escalated | the NT 10-K (10-K filed same day) **and an auditor change, Mar 2024** |
| Crown Castle FY2024 | clear | ✅ brief | — (late filing for a strategic review) |
| Brookfield AM FY2024 | clear | ✅ brief | — (procedural late filing) |
| Western Digital FY2024 | clear | ✅ brief | — (only a quarterly report late) |
| Rocket FY2024 | clear | ✅ brief | — (10-K/A with no figures) |

Two small inaccuracies in compliance's reasoning, verdicts unaffected: it called
ADM's 4.02 an "SEC non-reliance order" (a 4.02 is filed by the company), and
counted three delisting notices for Super Micro where the tool listed four.

## Why the four clear cases escalated

**Corpay, Symbotic — a remaining tool gap.** The flag says a 4.02 notice exists
and when; it does not say *which periods it withdraws*. Both withdrew only
interim quarters that the annual report later superseded — exactly the
distinction the labelling rule turns on — and compliance, unable to tell,
escalated. Fix: put the notice's own scope into the flag (the opening of its
Item 4.02 paragraph names the periods). One extra EDGAR fetch per 4.02, free.

**SiTime, MasTec — the labels may be wrong, not compliance.** Both had an
auditor change (8-K Item 4.01) that the curation scan never looked for, so the
rationale for "clear" never weighed it. The labelling rule does not treat an
auditor change as an escalation trigger; compliance did. Whether it should
depends on *why* the auditor left — the 4.01 states whether there were
disagreements — which the flag does not yet carry. **These two need a human
decision**, and the boundary cases' rule may need an auditor-change clause.

So of the 50% false-escalate rate, half is a fixable tool gap and half is an
open labelling question.

## Figures in the four briefs

5 of 7 tested figures correct. Losses kept their sign end to end (Crown Castle
−$3.90B net income, −$8.98 EPS; Western Digital −$0.80B) — #56 working in a
live run. Misses: Rocket net income shown as "$0.03B" against $29.0M — the
brief rounds to hundredths of a billion, too coarse for a small figure; Brookfield
net income $2.17B vs $2.108B (likely a different net-income line); Western
Digital EPS −$1.72 vs −$2.61 (unexplained from what is stored).

## Next

1. Carry the scope of a 4.02 notice (periods withdrawn) and of a 4.01 (resigned
   vs dismissed, any disagreements) into the flag, then re-run the slice.
2. Decide whether an auditor change belongs in the escalation rule; relabel
   SiTime and MasTec if so.
3. Grow the escalate side past 5 cases — the 0% false-clear rate is on n = 5.
