# Hardening Plan

Output of a `/grill-with-docs` session (2026-09-13). Decisions and rationale are recorded as ADRs in [`docs/adr/`](./adr/); the ubiquitous language is in [`CONTEXT.md`](../CONTEXT.md). This file is the actionable punch-list.

North star ([ADR-0001](./adr/0001-optimize-for-legibility-over-production-hardening.md)): this is a job-search / reference artifact — optimize for correctness, one-pass legibility, and clean extensibility; **not** scale, tenancy, or latency. Scaling is a talking-point, never premature abstraction in the diff.

Tracking issues: [#1–#12](https://github.com/ibs12/Multi-Tool-Research-Agent/issues).

## Order of work

`A → B → C`. The A-bugs are independent of each other; B builds on the calculator deletion (A4) and adds the ordering guarantee A3 relies on; C is config.

### A — Correctness bugs

| # | Issue | Summary |
|---|---|---|
| A1 | [#1](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/1) | `rag_search` always reports `success=True` — the `[RAG Error]` sentinel is buried behind `ingest_status`; ingest can also raise. Make failure detectable regardless of composition; wrap ingest so the node never raises. |
| A2 | [#2](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/2) | `tool_results`/`tools_called` duplication — `web_search`/`wikipedia`/`arxiv` return the full accumulated list; the reducer double-counts. Return deltas only; drop dead `tools_remaining` returns. |
| A3 | [#3](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/3) | RAG ingest skip is fuzzy (false skips → empty-but-"successful" RAG) and permanent (stale filings). Replace with an exact per-filing-`source_url` check. Depends on B5's ordering guarantee. |
| A4 | [#4](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/4) | Delete the vestigial `calculator` node — it injects a hardcoded `100·1.08⁵` into financial answers. Keep the inline `calculate_ratio`. |

### B — Structural hardening

| # | Issue | Summary |
|---|---|---|
| B5 | [#5](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/5) | `PREREQUISITES = {"rag_search": ["sec_edgar"]}` at the dispatcher: defer, auto-inject-once, drop-with-loud-error. Delete the prose rule + force-queue. ([ADR-0002](./adr/0002-tool-ordering-is-structural-not-prompted.md)) |
| B6 | [#6](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/6) | Record `termination_reason` (`completed` \| `iteration_budget_exhausted` \| `no_new_tools`); surface into synthesis + API/CLI. |
| B7 | [#7](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/7) | One shared error-prefix constant per tool, imported by both tool and node. ([ADR-0003](./adr/0003-tools-report-failure-via-tagged-error-string.md)) |

### C — Config

| # | Issue | Summary |
|---|---|---|
| C8 | [#8](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/8) | Default `CLAUDE_MODEL` → `claude-opus-4-8` (single flagship model). Clean drop-in; note the thinking/`max_tokens` caveat for a future Sonnet 5 move. (Not an ADR — a reversible env-var default.) |

### D — Cleanups / lower priority

| # | Issue | Summary |
|---|---|---|
| D9 | [#9](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/9) | Optional `GUARDS` boundary for `consensus_estimates` (already self-guards). Keep separate from `PREREQUISITES` — see CONTEXT.md. |
| D10 | [#10](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/10) | Shrink/remove `FinancialContext` — after A4 it carries only `sector` to only `arxiv`. |
| D11 | [#11](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/11) | Re-sync README/CLAUDE.md after A4 + C8; **fix the CLAUDE.md reducer note** (now known-wrong per A2). |
| D12 | [#12](https://github.com/ibs12/Multi-Tool-Research-Agent/issues/12) | ChromaDB smoke test so the fallback path doesn't rot; light structured logging. ([ADR-0005](./adr/0005-dual-vector-store-backend.md)) |

## Out of band

`.env` holds live-looking `ANTHROPIC_API_KEY` / `TAVILY_API_KEY` — gitignored, but rotate if that file has ever been shared.
