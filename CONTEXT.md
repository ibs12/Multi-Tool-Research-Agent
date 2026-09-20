# Multi-Tool Research Agent

Ubiquitous language for the agent's tool-orchestration model. Hard constraints between tools are enforced in code; soft preferences live in the supervisor prompt.

## Language

**Prerequisite**:
An ordering dependency between tools — tool A has a prerequisite on tool B when A consumes data B produced, so B must finish in an earlier iteration. A hard constraint, enforced structurally in code rather than left to the model to honor.
_Avoid_: dependency, guard.

**Guard**:
A precondition on a single tool's own inputs — whether that tool can validly run right now (e.g. a required ticker symbol is present). Answers "is this input valid?", never "what must have run first".
_Avoid_: prerequisite, validation, check.

## Watchlist language

The agent is organised around companies tracked over time, not one-off queries.

**Watchlist**:
The set of companies being tracked. Membership is the user's choice; it carries no
position data — no share counts, no cost basis. A watchlist says "tell me when this
changes", never "I own this".
_Avoid_: portfolio, holdings (both imply positions, deliberately out of scope).

**Signal**:
A cheap, deterministic, LLM-free indication that a tracked company may have changed —
a new SEC filing for its CIK, a material price move. Signals are polled continuously
because they are free; they decide *whether* to spend a Refresh, and are never
themselves the answer.
_Avoid_: trigger, alert (an alert is what the user is shown; a signal is what the
system detected).

**Refresh**:
One full agent run against a tracked company, producing a Run. Expensive (minutes,
real spend), so a Refresh is normally gated by a Signal rather than scheduled blindly.
_Avoid_: update, sync, poll (polling is what Signals do).
