# Boundary slice with scoped red flags + the auditor rule — 2026-11-08

**What changed since the previous run** ([after the SEC fix](2026-11-08-boundary-after-sec-fix.md)):
each 8-K 4.02 / 4.01 flag now carries a scope line read from the filing — does
the notice withdraw interim statements only or the annual report; did the
auditor resign or was it replaced, and does the filing explicitly report no
disagreements. The boundary rule gained (c): an auditor resignation, or
reported disagreements, escalates; a routine rotation does not. Under (c)
SiTime and MasTec stay *clear* (both routine rotations, verified in their
filings) and Super Micro gains EY's resignation as evidence. Compliance prompt
unchanged. 13/13 finished, no errors.

Raw scorer output: [`2026-11-08-boundary-scoped-flags.report.json`](2026-11-08-boundary-scoped-flags.report.json).

## The three runs

| | Before #64 | After #64 | **+ scope lines & rule (c)** |
|---|---|---|---|
| Boundary false-clear (should escalate, cleared) | 5/5 = 100% | 0/5 = 0% | **0/5 = 0%** |
| False-escalate (should clear, escalated) | — | 4/8 = 50% | **2/8 = 25%** |
| Fabricated figures | 0 | 0 | **0** |

## Per case

| Case | Expected | Previous run | This run | What compliance cited |
|---|---|---|---|---|
| ADM FY2023 | escalate | ✅ | ✅ escalated | 4.02 on the FY2023 statements |
| Axon FY2024 | escalate | ✅ | ✅ escalated | 4.02 — "withdrawing its FY2024 audited financial statements" |
| Monolithic Power FY2024 | escalate | ✅ | ✅ escalated | 4.02 on the audited FY2024 statements |
| Super Micro FY2024 | escalate | ✅ | ✅ escalated | EY's resignation (Oct 2024) |
| Autodesk FY2024 | escalate | ✅ | ✅ escalated — **wrong reason** | a GAAP vs non-GAAP EPS "conflict", not the NT 10-K |
| Corpay FY2023 | clear | ❌ | ✅ **brief** | — the scope line showed the 4.02 was interim-only |
| MasTec FY2023 | clear | ❌ | ✅ **brief** | — the scope line showed a routine rotation |
| Symbotic FY2024 | clear | ❌ | ❌ escalated | the interim 4.02 *and* the late FY2024 10-K together |
| SiTime FY2023 | clear | ❌ | ❌ escalated — **new reason** | "wrong-company data" in the evidence |
| Crown Castle FY2024 | clear | ✅ | ✅ brief | — |
| Brookfield AM FY2024 | clear | ✅ | ✅ brief | — |
| Western Digital FY2024 | clear | ✅ | ✅ brief | — |
| Rocket FY2024 | clear | ✅ | ✅ brief | — |

## What the remaining misses are

**Symbotic — a defensible disagreement with the label.** Two flags land on the
requested year: a 4.02 withdrawing the Q1–Q3 FY2024 interims, and a late FY2024
10-K, late *because* of correcting that error. The labelling rule clears it (the
annual statements were never withdrawn and were only a week late); compliance
weighed the two together. The case was already marked "close to the line" when
curated. Reasonable people could label it either way.

**SiTime — a different failure, upstream of compliance.** It escalated because
the evidence contained another company's data. The SEC tool returns SiTime's
own filings with or without a ticker (checked), so the contamination came from
another tool; the checkpoint keeps only the escalation reason, not each tool's
output, so which one can't be shown after the fact. Compliance refusing a brief
built on the wrong company is correct behaviour — the bug is in the research.

**Autodesk — right verdict, wrong reason.** It escalated over a "conflict"
between Yahoo's GAAP diluted EPS and analysts' adjusted EPS — two definitions,
not two contradictory sources. This is a side effect of #57 (reported EPS is now
GAAP diluted; consensus is adjusted). Monolithic Power's previous escalation
mentioned the same "conflict". It did not flip a verdict here, but it could on a
clean company.

## Figures in the six briefs

8 of 11 tested figures correct (Corpay, MasTec and Crown Castle 2/2). The three
misses are unchanged from the previous run: Brookfield net income (likely a
different net-income line), Western Digital EPS (unexplained), and Rocket net
income shown as "$0.03B" against $29.0M — the brief rounds to hundredths of a
billion, too coarse for a small figure.

## Next

1. Label EPS definitions in the evidence ("GAAP diluted" vs "adjusted, analyst
   consensus") so compliance stops reading them as a conflict.
2. Store each tool's output (or the evidence digest) in the eval checkpoint, so a
   failure like SiTime's can be traced to its tool.
3. Show small figures in millions in the brief (Rocket).
4. Decide Symbotic's label; grow the escalate side past n = 5.
