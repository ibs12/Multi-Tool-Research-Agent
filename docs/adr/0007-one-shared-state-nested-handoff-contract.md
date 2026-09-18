# Three agents share one `AgentState` with a nested handoff contract, not subgraphs

With peer handoff chosen ([ADR-0006](./0006-peer-handoff-via-command-goto.md)), the agents need a state model. Two shapes were on the table: one shared `AgentState` all three read and write, or one subgraph per agent with isolated state and an explicit boundary contract.

## Considered options

- **Subgraph-encapsulated agents.** Each agent is a compiled subgraph with private state; handoffs cross the boundary. Rejected:
  - `Command(goto=…)` is a flat-graph primitive; crossing a subgraph boundary requires `Command(goto=next, graph=Command.PARENT)` — so subgraphs build a boundary the handoff mechanism must immediately reach through. The isolation is punctured on every transition.
  - All three agents query the same filings via the RAG-MCP server. Subgraphs would force duplicating the evidence into each subgraph or syncing it across the boundary every hop — isolation nothing here needs. The extensibility axis this project cares about (ADR-0001) is "5–10 more *tools*", not "5–10 more *agents*".
- **One shared `AgentState` (chosen).** Flat state, all agents in one graph.

## Mechanism

A grab-bag flat state is avoided with a **nested** shape rather than a subgraph wall:

- `state["handoff"]` — the **single-writer cross-agent contract**: `research_findings`, `risk_assessment`, `compliance_verdict`, `escalation`. Each field is written by exactly one agent and read by the next. A nested key won't drift the way loose top-level fields would.
- `state["research_loop"]` — the existing supervisor/dispatcher loop internals, unchanged.
- `tool_results` stays top-level with the `append_list` reducer, because all three agents append their own RAG-MCP calls to it concurrently (the reducer is exactly why this is safe — issue #2).

## Consequences

- Roster and boundaries: **Research** (evidence-gathering, reused loop) → **Risk-analyst** (investment-risk read; may hand back if evidence is thin) → **Compliance-checker** (defensibility + regulatory + escalation authority; verdict `clear`/`needs-revision`/`escalate`). The final brief is synthesis as a post-compliance step ([ADR-0004](./0004-synthesis-runs-outside-the-graph.md)), triggered by a `clear` verdict.
- Once risk-analyst and compliance-checker issue their **own** RAG-MCP calls, `PREREQUISITES` ([ADR-0002](./0002-tool-ordering-is-structural-not-prompted.md)) may need entries for same-batch hazards between their outputs — the same class as `sec_edgar → ingest_document`. Deferred until those calls exist (a real dependency, not a speculative one).
