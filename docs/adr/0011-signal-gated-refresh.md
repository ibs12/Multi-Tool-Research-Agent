# Refreshes are gated by cheap signals, not scheduled blindly

The agent becomes a watchlist tool: companies tracked over time rather than one-off queries. That needs freshness — but a Refresh is one full multi-agent run, which costs minutes of wall-clock and real money. Something has to decide *when* to spend one.

## Considered options

- **Manual only.** You refresh a company when you think of it. No infrastructure, total cost control — but the "what changed since I last looked" screen only works if you remembered to look, which defeats the purpose of tracking.
- **Scheduled batch.** Refresh the whole watchlist nightly. Simple and predictable, and the reason it was rejected is arithmetic: ~10 companies × nightly × ~$0.35 ≈ **$105/month**, the overwhelming majority of it spent re-researching companies where nothing happened.
- **Signal-gated (chosen).** Poll a free, deterministic indicator continuously; spend a Refresh only when it fires. ~10 companies × ~8 filings/year ≈ **~$28/year**, plus manual refreshes.

## Mechanism

A **Signal** is cheap, deterministic and LLM-free (see `CONTEXT.md`):

- a new 10-K / 10-Q / 8-K for a tracked company's CIK — detectable through the SEC XBRL/EDGAR client already written for the eval (`eval/frames_client.py`), no model call;
- a material price move **since the company's last Refresh**, via the free yfinance path the consensus tool already uses. Measured from the last close that Refresh could have seen, not over a trailing window: a window re-reports a move the agent has already researched, buying the same Refresh on every sweep until the move scrolls out.

Signals are polled because polling is free. A Refresh is fired only when one trips. The expensive tier stays rare by construction rather than by discipline.

## Consequences

- **A mostly-quiet screen is the correct output.** "Six companies: nothing filed, nothing to do" is the honest answer most days, and it costs nothing to produce. The UI must present quiet as a result, not as emptiness.
- **The worker must record that it ran even when nothing changed.** Otherwise silence is ambiguous between "no signals" and "the cron died", and a tool you trust for money decisions cannot have that ambiguity.
- **Blind spot, accepted deliberately:** a lawsuit, a CEO departure, a short-seller report — none of these fire a Signal until they reach a filing or move the price. A news signal would close the gap and is deliberately deferred; manual Refresh covers it meanwhile.
- The cheap tier is *reuse*, not new infrastructure: the change-detector was built as eval ground-truth tooling (R2/E6) and already knows how to ask EDGAR what a filer has filed.
