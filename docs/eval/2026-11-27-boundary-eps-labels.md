# Boundary slice after EPS labels — 2026-11-27

**What changed since the [scoped-flags run](2026-11-08-boundary-scoped-flags.md):**
#68 (reported EPS labelled "GAAP, as reported", consensus EPS labelled adjusted,
sub-$1B figures in millions, each tool's output kept in the result) and #69
(parser/delta noise fixes). Compliance prompt unchanged. Same 13 cases, `multi`
arm, `claude-sonnet-4-6`. 13/13 finished, no errors.

Raw scorer output: [`2026-11-27-boundary-eps-labels.report.json`](2026-11-27-boundary-eps-labels.report.json).

## The four runs

| | Before #64 | After #64 | Scoped flags | **EPS labels (this run)** |
|---|---|---|---|---|
| Boundary false-clear (should escalate, cleared) | 5/5 | 0/5 | 0/5 | **1/5 = 20%** |
| False-escalate (should clear, escalated) | — | 4/8 | 2/8 | **1/8 = 12.5%** |
| Cases right overall | — | 9/13 | 11/13 | **11/13** |
| Fabricated figures | 0 | 0 | 0 | **0** |

## What moved

- **SiTime now clears (correct).** The previous run's "wrong-company data" did
  not recur; with the auditor change scoped as routine, compliance cleared it.
- **No escalation cites an EPS "conflict" any more.** The four correct
  escalations cite the 4.02 notices (ADM, Axon, Monolithic Power) and the
  auditor resignation (Super Micro).
- **Autodesk now clears (wrong).** Its flags were in the evidence — the stored
  SEC output shows the NT 10-K for the period ending 2024-01-31 and a delisting
  notice — but compliance returned needs-revision and the brief shipped. Across
  three post-#64 runs Autodesk escalated on the NT 10-K once, on the spurious
  EPS conflict once, and cleared once: the NT 10-K alone does not reliably
  trigger an escalation. Unlike 4.02 and 4.01 flags it carries no scope line —
  nothing says the 10-K was ~10 weeks late or that the cause was an
  audit-committee investigation, which is what the labelling rule turns on.
- **Symbotic still escalates** (interim-only 4.02 plus a late 10-K for the same
  year) — the standing disagreement with its label.

## Reading the numbers

With 5 and 8 cases, one case is 20 and 12.5 points, and Autodesk shows a single
case can flip between runs. The stable findings across the three post-#64 runs:
the three 4.02-on-annual cases and Super Micro escalate every time; Crown
Castle, Brookfield, Western Digital and Rocket clear every time; Corpay and
MasTec clear once the flag says what it covers. Autodesk and Symbotic are the
unsettled ones.

## Figures in the eight briefs

13 of 15 tested figures correct. Rocket's net income now reads correctly in
millions (#68). Still wrong: Brookfield net income and Western Digital EPS, as
in every run.

## Next

1. Give NT 10-K flags a scope line too: how late the report was (10-K filing
   date vs the notice) and the stated reason from Part III of the notice.
2. Decide Symbotic's label.
3. Run each case more than once before quoting a rate; grow the escalate side.
