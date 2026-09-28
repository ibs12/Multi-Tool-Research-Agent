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

- **Fiscal-year labels** follow the filer, not the SEC frame: a frame is keyed by
  *calendar* year, so for a January year-end `CY2024` is the company's FY2025.
  Cases are labelled by the year the fiscal year ends, taken one week back so
  52/53-week years closing on 1–3 January stay in the year they cover (#58).
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

## The boundary slice (E6.Q3) — curated verdicts, sourced facts

Boundary cases are ones where a reasonable compliance reviewer could plausibly
go either way, each with its verdict assigned **per case** and a written
rationale. They live in `boundary_cases.jsonl`, built by
`build_boundary_cases.py`:

```bash
PYTHONPATH=. python eval/build_boundary_cases.py   # re-checks every cited filing on EDGAR
PGVECTOR_URL= python eval/run_full.py --arm multi --boundary-only   # ~13 runs
```

**Which cases and which verdict are human judgements; nothing factual is typed
in.** Every figure comes from SEC XBRL, and every filing a rationale cites is
looked up on EDGAR at build time — the build fails if one is missing.

**The labelling rule**, from the compliance policy (escalate for "a regulatory
red flag", "core figures unverifiable", "sources conflict irreconcilably"):

- **escalate** — the requested year's *annual* statements carry the flag:
  (a) an 8-K Item 4.02 non-reliance notice on them, or (b) the annual report
  filed materially late because of a review of the company's own accounting or
  controls.
- **clear** — the flag is adjacent, not on them: non-reliance limited to interim
  quarters, a late filing by days or for procedural/strategic reasons, an
  amendment that changed no figures.

They are *boundary* cases because the figures eventually filed look normal and
audited in every one; the flag (or its absence) lives in a different filing. A
figures-only review clears all of them. The candidates were found by scanning
the ~700 largest SEC filers for 2024–2026 non-reliance notices and late
filings, and every case sits in FY2023–FY2024, inside the years the research
tools reach — outside that window a case escalates for lack of data, which
measures scope, not judgement.

Current slice: **13 cases — 5 escalate, 8 clear.** That is below the ~10
escalate the original design targeted; the boundary false-clear rate is
therefore computed over 5 cases and should be read as indicative. Three of the
escalations are deliberately the closest calls (ADM FY2023, Axon FY2024,
Autodesk FY2024 — the flag touched a segment note, a balance-sheet
classification, or non-GAAP metrics, not the reported totals). Review those
labels first if you disagree with the rule.

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
- Forward-estimate tested fields (consensus band, E1/R2) are not yet sourced —
  they need analyst consensus (yfinance), not XBRL.
