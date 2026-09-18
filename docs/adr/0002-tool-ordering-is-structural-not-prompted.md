# Hard tool ordering is enforced structurally, not by the supervisor prompt

Some tools consume another tool's output from shared state (e.g. `rag_search` ingests the filing URLs that `sec_edgar` writes to `tool_results`). Previously this ordering was protected only by a prose rule in the supervisor's system prompt ("NEVER put sec_edgar and rag_search in the same list") plus a partial force-queue fallback — which a single same-batch schedule from the model defeats, causing the dependent tool to run against empty/stale state and silently produce wrong figures. We move **hard** ordering constraints out of the prompt and into code, and keep only **soft** preferences ("prefer web + wikipedia first") in the prompt.

## Considered options

- **Prompt owns ordering (status quo).** A correctness-critical invariant depends on the model reading and obeying a sentence; a trace shows a same-batch schedule breaks it. Rejected.
- **Full declarative framework** — tools declare metadata, prompt auto-assembles, a dependency-graph resolver orders them. Rejected as premature abstraction under [ADR-0001](./0001-optimize-for-legibility-over-production-hardening.md): a resolver is more code to read than the bug it prevents, for a 7→15-tool demo.
- **Targeted structural fact (chosen).** One dict, enforced at the dispatcher boundary. Smaller than the problem it solves.

## Mechanism

```python
# A depends on B  ⟺  A consumes B's dispatcher-written output from shared state.
PREREQUISITES = {"rag_search": ["sec_edgar"]}
```

At the dispatcher boundary, a queued tool whose prerequisites are not yet in `tools_called` is **deferred**. Deferral alone can stall forever if the model never schedules the prerequisite, so:

- If the prerequisite has **not run yet**, the dispatcher **auto-injects** it into the batch (deterministic self-heal — "you asked for rag but forgot sec_edgar; I'll run sec_edgar first").
- If the prerequisite has **already run and failed**, the dependent tool is **dropped with a loud error `ToolResult`** and the run's `termination_reason` records it — never computed on empty state.
- Injection is capped at 1 per tool so it cannot loop.

Membership in `PREREQUISITES` is governed by the rule in the comment, which is what keeps the dict complete (the next dependent tool is a one-line add) and free of spurious entries (audited: of the seven tools, only `rag_search` consumes a sibling's output).

## Consequences

- **Argument/state preconditions are a separate mechanism, not `PREREQUISITES` entries.** "`consensus_estimates` needs a ticker" answers "is this input valid?", not "what must have run first". It stays a guard clause (the tool already self-guards, returning a clean error when no ticker is present). A ticker check does not belong in a dict keyed by tool names.
- **No dispatcher-scheduled calculator.** `calculate_ratio` runs synchronously inside the supervisor on params the model already extracted, so it is correctly ordered by construction and needs no dispatcher entry. A dispatcher calculator would be reintroduced only for a real case that combines two or more async tools' outputs — with an actual `PREREQUISITES` entry — never on spec. (The former vestigial `calculator` tool node was deleted; it injected a hardcoded figure into financial answers with no signal it was fake.)
