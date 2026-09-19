# Map 2 — eval suite

Field-level extraction accuracy, false-clear / false-escalate rate, and citation
correctness for the research agent, with a defensible single-agent-vs-multi-agent
comparison. This directory currently holds **E6 — case sourcing & ground-truth
construction**; scoring (E2/E3), tracking (E4), and the before/after (E5) build on it.

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

## Known-pending (honest status)

- The `missing_conflicting` bucket currently sources the **missing** signal
  (untagged concept). The **conflicting** signal (two concepts that should
  reconcile but disagree beyond tolerance) is not yet implemented.
- Boundary cases (`boundary_cases.jsonl`) await hand-curation.
- Forward-estimate tested fields (consensus band, E1/R2) are not yet sourced —
  they need analyst consensus (yfinance), not XBRL.
