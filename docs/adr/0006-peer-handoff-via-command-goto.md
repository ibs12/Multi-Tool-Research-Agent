# Multi-agent control is peer handoff via `Command(goto=…)`, not a supervisor-router

Milestone 1 extends the single research loop into a **research → risk-analyst → compliance-checker** pipeline. The agents run in a known order with a few conditional back-edges (compliance needs more evidence → research) and one escalation exit ([ADR-0009](./0009-human-escalation-is-a-terminal-state.md)). The question is what mechanism transfers control between them.

## Considered options

- **Agent-as-tool** — a sub-agent that *returns a value* to a caller who retains control. Rejected: a caller that keeps control and loops over sub-agents-as-tools **is** the "one agent calling tools in a loop" that milestone 1 exists to move away from. It would relabel the current design, not change it.
- **Supervisor-router (hierarchical hub).** A central node inspects state each turn and dynamically routes to the next agent. Rejected under [ADR-0001](./0001-optimize-for-legibility-over-production-hardening.md): for a **known-order** pipeline a dynamic hub *rediscovers* fixed routing on every run. Encoding the handoff edges directly is more legible than a router that re-derives them. Revisit only if agent selection becomes genuinely unpredictable (frequent, non-deterministic) rather than a pipeline-with-hand-backs.
- **Peer handoff via `Command(goto=…)` (chosen).** Each agent is a first-class node that, when done, returns a `Command` naming its own next hop. No hub; the routing lives in the agents.

## Mechanism

An agent node returns `Command(goto=<next_agent>, update={...})`. LangGraph's `Command` is a **flat-graph primitive** (`langgraph.types.Command`) — `goto` names a sibling node in the same graph. The pipeline edges are therefore explicit and few, and the graph reads top-to-bottom; each agent owns its next-hop decision (proceed / hand back / escalate).

## Consequences

- The existing `supervisor → dispatcher` loop becomes the **research agent** ([ADR-0007](./0007-one-shared-state-nested-handoff-contract.md)), unchanged internally.
- Because `Command(goto=)` cannot cleanly cross a subgraph boundary (it would need `graph=Command.PARENT`), the peer-handoff choice drives the flat-state decision in [ADR-0007](./0007-one-shared-state-nested-handoff-contract.md) — the two ADRs are mutually reinforcing.
- Human escalation ([ADR-0009](./0009-human-escalation-is-a-terminal-state.md)) is just another handoff edge — to a terminal outcome instead of a peer.
- Hand-rolled `Command` handoffs were chosen over a prebuilt `langgraph-swarm`: for three agents, explicit edges read better than a framework abstraction (ADR-0001).
