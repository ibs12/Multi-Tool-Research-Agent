# Map 2 — eval suite

Field-level extraction accuracy, false-clear / false-escalate rate, and citation
correctness for the research agent, with a defensible single-agent-vs-multi-agent
comparison. Holds **E6** (dataset), **E2/E3** (`scoring.py`), **E4** (`tracking.py`,
`run_eval.py`, two-tier CI), and **E5** (`compare.py`, the before/after).

## E5 before/after — small demo findings (7 cases, `demo_cases.jsonl`)

A live 7-case demo (5 clean large-caps + 2 escalation-trigger queries) validated
the whole pipeline **and** surfaced real findings:

- **Capability claim holds:** single-agent escalated **0** (no mechanism);
  multi-agent escalated **6**. An added capability, not a pp delta.
- **The false-escalate guardrail earned its keep (E3 validation):** multi's
  false-clear was **0.0** — which alone looks perfect — but its **false-escalate
  was 0.80** (it escalated 4 of 5 *clean* large-caps on `"FY{year} financial
  results"` queries). Reporting false-clear *paired with* false-escalate (the E3
  decision) is exactly what exposes this; false-clear alone would have hidden it.
- **The pre-registered decision rule correctly returned "mixed / does not clear
  the bar"** (false-escalate failed) — honest-null handling working (E5.Q5).
- **Latency:** multi ~5× single (2713s vs 543s for 7 cases).

Three follow-ups this exposed — now addressed:
1. **Multi over-escalated period-specific queries** → **fixed** (Map 1
   calibration): `compliance_checker_node` no longer escalates an unresolved
   `needs-revision` at the hand-back cap — a fixable/minor gap ships a caveated
   brief; escalation is reserved for the explicit `escalate` verdict. The prompt's
   escalate bar was sharpened ("do not escalate merely because coverage is
   incomplete").
2. **`extract_fields` under-matched** → **fixed** (E2.Q3): the parser dropped the
   markdown table's outer-pipe empties (the label sat at the wrong index); it now
   reads the values correctly (Apple FY2023 → 3/4 fields, EPS legitimately
   omitted). Citation is scored only when the run supplies an accession (briefs
   cite source *types*), so it reads "not measured" rather than a false 0.
3. **should-escalate needs live escalation *queries*** → **added**
   `live_escalation_cases.jsonl`: 10 curated private-company queries (SpaceX,
   Stripe, OpenAI, …) with no SEC filings, where a defensible live run should
   escalate. Distinct from the dataset's synthetic label-perturbation cases,
   which are for *static* scoring of recorded runs, not live runs.

With these in, the full 154×2 headline run is a compute/time decision, not a
correctness one.

## Layout

| Path | What |
|---|---|
| `frames_client.py` | SEC XBRL client (`frames` + `companyfacts`), User-Agent + rate-limit + on-disk cache (`.cache/`, gitignored). The R2 ground-truth source. |
| `schema.py` | The E1 case label schema + JSONL IO + `validate()`. |
| `generate_dataset.py` | The seeded, versioned generator. |
| `dataset/cases.jsonl` | The committed generated dataset (the frozen artifact E5 scores). |
| `dataset/manifest.json` | Version, seed, counts, provenance. |
| `boundary_cases.jsonl` | The **hand-curated** boundary slice (E6.Q3) — see below. |

## Regenerate

```bash
PYTHONPATH=. python eval/generate_dataset.py     # live SEC calls (cached); seeded → reproducible
```

## How ground truth is built (E6)

All categories come from **detectable structural signals in SEC XBRL**, not
hand-eyeballing at volume (the TakeMeter antidote):

- **correct_extraction** — frames-canonical annual values (revenue = union of
  revenue concepts, net income, EPS, gross margin = GrossProfit÷Revenue), each
  field pinned to its own source accession.
- **ambiguous** — material full-year **restatements**: one `(start, end)` period
  with ≥2 accessions whose values differ >1%. Scored against the
  as-originally-filed accession (Apple FY2008-style). Covers net income + revenue.
- **missing_conflicting** — a tested concept **not tagged** (e.g. banks have no
  `GrossProfit` ⇒ gross margin undisclosed). The system must say "not disclosed",
  not fabricate. `gap_type = missing_disclosure`.
- **should_escalate** — (a) `10-K/A` amendments that **actually changed** a
  tested figure beyond tolerance (E6.Q2 narrowing — a bare amendment is *not* a
  signal); (b) controlled **synthetic perturbation** (a citation made
  unverifiable), flagged `synthetic: true`.

## The boundary slice (E6.Q3) — human curation, not generated

E1 reserves ~1/3 of the should-escalate + missing/conflicting cases as **boundary
cases**: ones where a reasonable compliance reviewer could plausibly go either
way, with `expected_verdict` assigned **per case** (not by bucket). Target ~18
(~10 should-escalate + ~8 missing/conflicting). These are **hand-authored** in
`boundary_cases.jsonl`, each with a written `notes` rationale for why it is
marginal — deliberately the one place hand-labeling earns its keep (bounded and
legible, the opposite of an opaque hand-labeled set). They are merged with the
generated set at scoring time and the false-clear rate is reported broken out
clear-cut vs boundary (E3).

## Known limitation — stale-prior escalation (the SpaceX case)

The first captured escalation run used *"Analyse SpaceX investment outlook"* and
the compliance-checker escalated it. That looked like a clean win — until it
wasn't: **SpaceX IPO'd in June 2026 under ticker SPCX.** So the market data the
agent retrieved (the SPCX quote, IPO, earnings) was *real*, and compliance
escalated by **dismissing legitimate current data as "fabricated" because it
conflicted with a training-era belief that SpaceX is private.** It escalated for
the *wrong reason*.

That is a genuine, distinct failure mode — the model's own prior overriding a
fresh, legitimate source — different in kind from E1's `missing_conflicting`
(which is *source-vs-source* disagreement). It is parked in the Map 2 Fog list as
a candidate future eval case type + a Map 1 calibration ticket (prefer current
primary-source data over stale knowledge).

Consequences, applied here:
- **Escalation triggers are now fictional companies, not "private" real ones.**
  `live_escalation_cases.jsonl` uses invented companies (Zephyr Dynamics, …) with
  no real existence — a **time-invariant** trigger. A real company's public/
  private status can silently flip and invalidate the case (exactly what SpaceX
  did), so that whole category of trigger is retired.
- **The committed demo fixture (`tests/fixtures/escalation_run.json`) is a
  fictional-company run**, where the escalation happens for the *right* reason
  (no verifiable primary source exists). The SpaceX run is NOT used as the demo —
  a "why did it escalate?" that answers "it distrusted real data" undercuts the
  safety narrative rather than showing it.

## Known-pending (honest status)

- The `missing_conflicting` bucket currently sources the **missing** signal
  (untagged concept). The **conflicting** signal (two concepts that should
  reconcile but disagree beyond tolerance) is not yet implemented.
- Boundary cases (`boundary_cases.jsonl`) await hand-curation.
- Forward-estimate tested fields (consensus band, E1/R2) are not yet sourced —
  they need analyst consensus (yfinance), not XBRL.
