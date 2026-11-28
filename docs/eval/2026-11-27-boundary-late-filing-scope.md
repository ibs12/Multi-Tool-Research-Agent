# Boundary slice with late-filing scope — 13/13, 2026-11-27

**What changed since the [EPS-labels run](2026-11-27-boundary-eps-labels.md):** #71 —
NT 10-K / NT 10-Q flags now say how late the report was, and a late annual
report quotes the company's stated reason. Compliance prompt unchanged. Same 13
cases, `multi` arm, `claude-sonnet-4-6`.

**Two results were re-run.** In the first pass Western Digital and Rocket were
"escalated" by compliance's fail-safe after a connection error (`Compliance
check could not run (Connection error.)`) — an outage, not a judgement. Those
two results were discarded and the two cases re-run; both then produced briefs.
The eval runner still scores such a fail-safe as an escalation; it should record
it as an error and retry (the worker already does, #63).

Raw scorer output: [`2026-11-27-boundary-late-filing-scope.report.json`](2026-11-27-boundary-late-filing-scope.report.json).

## The five runs

| | Before #64 | After #64 | Scoped flags | EPS labels | **Late-filing scope** |
|---|---|---|---|---|---|
| Boundary false-clear | 5/5 | 0/5 | 0/5 | 1/5 | **0/5** |
| False-escalate | — | 4/8 | 2/8 | 1/8 | **0/8** |
| Cases right | — | 9/13 | 11/13 | 11/13 | **13/13** |
| Fabricated figures | 0 | 0 | 0 | 0 | **0** |

## What moved

- **Autodesk escalates, for the right reason** — "an NT 10-K citing an active
  Audit Committee internal investigation with outside counsel", i.e. the quoted
  reason #71 added. In the previous run, with the bare flag, its brief shipped.
- **Symbotic clears for the first time.** Its NT 10-K flag now reads "report
  filed 7 days after this notice … correcting an error related to system
  revenue recognition", next to a 4.02 scoped as interim-only. With both scoped,
  compliance asked for a revision and let the brief ship — the curated label.
- All other cases as before: ADM, Axon, Monolithic Power on their 4.02 notices,
  Super Micro on EY's resignation; the remaining clear cases clear.

## How far to trust it

This is one pass over 13 cases, and Autodesk and Symbotic have each landed on
both sides across runs. 13/13 shows that with scoped flags compliance *can* draw
every line the labelling rule draws; it does not show it always will. Repeat
passes are needed before quoting a rate.

## Figures in the eight briefs

12 of 15 tested figures correct. Wrong: Brookfield net income and Western
Digital EPS (both in every run), and Symbotic EPS (first time Symbotic produced
a brief).

## Next

1. Run the slice three times and report per-case agreement.
2. Make the eval runner treat a compliance fail-safe as an error to retry, not
   an escalation.
3. Grow the escalate side past 5 cases.
