# "What changed" compares structured figures, not brief prose

A watchlist is only worth having if it can tell you what moved since last time. That requires deciding what a *delta* is computed over — which in turn decides what every Run must store.

## Considered options

- **Diff the brief text.** Rejected outright. The model rewords its prose on every run, so a company where genuinely nothing happened still produces a diff that looks enormous. The screen would become untrustworthy within a week, and an untrustworthy change feed is worse than none.
- **Stance only** — track just the Analyst Verdict (Bullish/Neutral/Bearish) and escalation state. Stable and cheap, but far too coarse to act on: it cannot tell you that forward EPS was cut 17%.
- **Structured field comparison (chosen).**

## Mechanism

Every Run stores the extracted figures — revenue, EPS, gross margin, net income, forward estimates — alongside the compliance verdict and the risk flags. A delta compares *those*:

```
NVDA · EPS FY2027E $9.58 → $11.20 (+17%) · risk flags 3 → 5 · verdict clear → escalate
```

Materiality is decided by the **eval's per-field tolerances** (ADR-era E2): ±0.5% on level figures, ±max($0.01, 0.5%) on EPS, ±0.1pp on margins. The same rule that decides whether an eval case passed decides whether a change is worth showing — so presentation rounding never surfaces as news.

Three pieces already exist and are reused rather than rebuilt: `eval/run_eval.py:extract_fields` (parses the mandated Financial Snapshot table), `eval/scoring.py` (the tolerance rules), and the run store, which already persists `compliance_verdict` and `risk_assessment` per run.

## Consequences

- **The brief becomes the secondary artifact.** The primary object is the structured figures plus the verdict; the prose is what you open when a number moves and you want to know why. This is the right trade for a decision tool — ten companies' figures are scannable, ten prose briefs are not — but it inverts what the UI is built around.
- **Extraction accuracy becomes load-bearing.** Deltas now drive decisions about money, and how often the extracted figures are wrong is *unmeasured*: the 154-case eval exists precisely to answer that and has not been run. This does not block building; it blocks *trusting*. A delta is only as good as the parse behind it.
- A field the parser fails to read must be rendered as **unknown**, never as a change. Silence from the extractor is not evidence of stability.
