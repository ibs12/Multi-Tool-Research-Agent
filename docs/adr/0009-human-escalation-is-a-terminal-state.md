# Human escalation is a terminal state, with an interrupt-shaped payload

The compliance-checker ([ADR-0007](./0007-one-shared-state-nested-handoff-contract.md)) can decide a case is not defensibly answerable and must go to a human — `verdict = escalate`. What does the graph *do* with that?

## Considered options

- **Flag-and-ship** — produce the brief anyway with an "escalated" flag. Rejected on semantics: it undercuts the meaning of escalation by shipping the very output escalation says shouldn't ship.
- **`interrupt()` + resume** — pause the graph, collect human input, resume. Rejected *for now*: it is real machinery (a checkpointer, a resume channel across CLI/API/SSE, a human-input UI) and — decisively — **incompatible with the Map 2 eval architecture**. 150–200 eval cases need escalation to resolve to a deterministic, scoreable outcome without a human resuming every escalate case in CI; `interrupt()` would force mocking a human resume per case just to unblock the graph, buying the eval nothing (the eval scores whether escalation was correctly *triggered*, not what the human then decides).
- **Terminal "escalated" state (chosen)** — `escalate` routes to a terminal outcome; no brief is auto-produced; the output is a structured **escalation package**.

## Mechanism

The `escalate` verdict routes to a terminal node that sets `termination_reason = "escalated"` and writes `state["handoff"]["escalation"]` — a structured package of accumulated research + risk findings + compliance's reasons (conflicting/missing evidence, what a human must decide). This is symmetric with `clear → synthesis` and `needs-revision → hand-back`, and reuses the existing `termination_reason` seam (issue #6) that the API/CLI already surface.

Two refinements make the "future upgrade" claim honest:

- **The payload is interrupt-shaped now.** The escalation package carries the same fields an `interrupt()` payload would. Upgrading later becomes swapping the terminal `END` for `interrupt()` with the *same object* — a genuine cheap swap, not a redesign.
- **Escalated runs render distinctly.** CLI and API show `ESCALATED: [reasons] + [evidence summary]`, not a silently-missing brief — escalation reads as a deliberate safety outcome, not a broken run. Costs nothing beyond what is already built.

## Consequences

- `interrupt`/resume is documented as a future upgrade, not built.
- This is the only escalation option compatible with the committed Map 2 eval running in CI without mocking human resume — the eval's false-clear / false-escalate metrics score the *trigger*, which this design produces deterministically.
