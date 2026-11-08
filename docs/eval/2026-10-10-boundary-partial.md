# Boundary false-clear rate — first measurement (partial), 2026-10-10

**What ran.** The boundary slice (`eval/boundary_cases.jsonl`, 13 cases, built by
`eval/build_boundary_cases.py`), `multi` arm, `claude-sonnet-4-6`, dataset
`e6-2026-09-27`. Local vector store; production untouched.

    PGVECTOR_URL= python eval/run_full.py --arm multi --boundary-only

**Partial.** The Anthropic account ran out of credit after the fifth case. The
eight results after that were the compliance fail-safe reacting to
`400 — credit balance too low`, not judgements, and were removed from the
checkpoint. The five kept results ran to completion with no tool failures.
Re-running the same command finishes the remaining eight and skips these five.
Raw scorer output: [`2026-10-10-boundary-partial.report.json`](2026-10-10-boundary-partial.report.json).

## Result: all five should-escalate cases were cleared

| Case | Why it should escalate | Outcome | Compliance verdict |
|---|---|---|---|
| ADM FY2023 | 8-K 4.02 on the FY2023 10-K; 10-K late during an SEC-prompted investigation | brief | clear |
| Axon FY2024 | 8-K 4.02 on the FY2024 annual statements | brief | clear |
| Monolithic Power FY2024 | 8-K 4.02 on the audited FY2024 statements | brief | needs-revision → shipped |
| Super Micro FY2024 | 10-K ~6 months late during a controls review | brief | clear |
| Autodesk FY2024 | 10-K ~2.5 months late during an audit-committee investigation | brief | needs-revision → shipped |

**Boundary false-clear rate: 5/5 = 100%** (n = 5 — indicative, but uniform).

## Why: the agent never sees these red flags

This is a tool blind spot, not a judgement call compliance got wrong.
`tools/sec_edgar.py` passes the agent only filings of type 10-K, 10-Q, 8-K or
DEF 14A, so:

- **NT 10-K (late filing) and 10-K/A (amendment) are filtered out entirely.**
- **An 8-K is listed only as "8-K".** EDGAR's submissions data includes each
  8-K's item codes — `4.02` is *non-reliance on previously issued financial
  statements* — but the tool drops them.

So the red flag never reaches the risk analyst or compliance from SEC, and web
search did not surface it either — even for Super Micro, whose late 10-K was
widely reported. Compliance applied its policy to evidence that looked clean.

## What would change it

Surface the flags the tool already downloads: keep NT 10-K and 10-K/A, and label
8-Ks with their items (e.g. "8-K — Item 4.02: Non-reliance on previously issued
financial statements"). Then re-run this slice: the escalate side measures
whether compliance acts on a flag it can see, and the eight clear cases measure
whether it over-reacts to flags that sit only *next to* the requested year.

## Not measured yet

The false-escalate side of the slice (8 clear-expected cases) — pending credit.
