# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A multi-tool financial research agent: given a query like `"Analyse Apple Inc. investment outlook"`, it runs a supervisor-driven LangGraph loop that calls research tools (web, Wikipedia, SEC EDGAR, RAG over 10-K/10-Q text, arXiv, calculator, analyst consensus), then synthesises a structured analyst brief with per-claim source citations. Ships as a CLI, a FastAPI + SSE server with a single-file frontend, and deploys to Railway.

## Commands

```bash
# Setup (venv is named myenv/ and is gitignored)
python -m venv myenv && source myenv/bin/activate
pip install -r requirements.txt

# CLI — --stream shows node-by-node progress, --cache writes to .agent_cache/
python run.py --stream "Analyse Apple Inc. investment outlook"

# API server (Swagger at /docs, frontend at /)
python server.py                 # 0.0.0.0:8000
python server.py --reload        # hot-reload for dev

# Full stack (pgvector + agent) or DB only
docker compose up -d
docker compose up pgvector -d

# Offline unit tests — Anthropic + vector store + HTTP all mocked, no network/keys needed
python -m pytest tests/test_rag.py tests/test_supervisor.py -v
python -m pytest tests/test_supervisor.py::test_routes_to_end_when_no_tools -v   # single test

# Integration test — a plain script, NOT pytest; requires a running server + real API keys
python tests/test_api.py

# Inspect the pgvector store directly
docker exec -it pgvector-financial psql -U postgres -d financial_agent \
  -c "SELECT company, form_type, filed_at, COUNT(*) FROM sec_filing_chunks GROUP BY 1,2,3;"
```

Requires `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` in `.env`. SEC EDGAR, arXiv, Wikipedia, and yfinance need no keys. The `sentence-transformers/all-MiniLM-L6-v2` model downloads on first RAG run.

## Architecture — the parts that span multiple files

**The graph does NOT produce the report.** `agent/graph.py` compiles four nodes — `supervisor` and `dispatcher` (the research loop) plus `risk_analyst` and `compliance_checker` (the multi-agent pipeline, ADR-0006/0007). Synthesis is deliberately *not* a graph node. Both entry points (`run.py`, `api/main.py`) drive the graph, then call `synthesis_node(result)` / `stream_synthesis(result)` separately. If you invoke the graph and expect `final_report` in the output, it won't be there — you must run synthesis yourself.

**Drive the graph through the ASYNC API.** The dispatcher is an async-only node, and langgraph's sync runner rejects it ("No synchronous function provided to dispatcher"). Use `graph.ainvoke()` / `graph.astream()`. `graph.invoke()` works only while the dispatcher never fires, which is why this bug hid for so long.

**`agent_mode` selects the pipeline at runtime, on one compiled graph.** `"multi"` (default) routes research → risk-analyst → compliance-checker; `"single"` exits to synthesis straight after research — the pre-multi-agent baseline on the identical substrate (E5). Set per request (`agent_mode` in the API body, a toggle in the frontend) or via the `AGENT_MODE` env var. The specialised agents hand off with `Command(goto=…)` and own their own next hop; compliance can clear, hand back once (capped by `MAX_HANDBACKS`), or escalate. An escalated run is terminal: `termination_reason="escalated"`, no brief, an interrupt-shaped package in `state["handoff"]["escalation"]` (ADR-0009). Every surface must render that distinctly rather than showing a missing report.

**Control flow lives entirely in the supervisor.** `agent/nodes/supervisor.py` calls Claude with the native tool-use API (typed `TOOL_SCHEMAS`, not string parsing). Claude returns a `plan_research` block whose `tools_to_call` becomes `state["tools_remaining"]`, and a `ready_to_synthesise` boolean. The routers in `graph.py` are dumb: `route_after_supervisor` goes to `dispatcher` iff `tools_remaining` is non-empty (else END); `route_after_dispatcher` loops back to `supervisor` until `iteration_count >= max_iterations`. Only the supervisor decides to stop — this prevents races when tools run concurrently.

**The dispatcher runs queued tools in parallel, honoring prerequisites.** `async_tool_dispatcher` in `graph.py` looks each tool up in `TOOL_REGISTRY` and runs them in dependency *waves* (ADR-0002): a tool whose `PREREQUISITES` haven't succeeded (e.g. `rag_search` needs `sec_edgar`) waits for a later wave, a missing prerequisite is auto-injected once, and a prerequisite that ran and failed drops the dependent tool with a loud error. Within a wave it `asyncio.gather`s: async nodes (`web_search`, `sec_edgar`, `rag_search`, `consensus_estimates`) awaited directly, sync nodes (`wikipedia`, `arxiv`) via `asyncio.to_thread`; `inspect.iscoroutinefunction` routes — there is **no ThreadPoolExecutor**. Adding a tool: implement `run_*` in `tools/` (return a tagged error string on failure via a per-tool `*_ERROR` constant — ADR-0003), add a node in `agent/nodes/tools.py`, register it in `TOOL_REGISTRY`, add it to the `tools_to_call` enum in `supervisor.py`, and add a `PREREQUISITES` entry if it consumes another tool's output. (Financial ratios are the exception — computed inline by the supervisor via `calculate_ratio`, not scheduled as a dispatcher tool.)

**Ordering constraint: `sec_edgar` must run before `rag_search`.** `rag_search` ingests the filing URLs `sec_edgar` discovers, but tools in one dispatcher batch all see the same pre-batch state snapshot. This is enforced **structurally**, not by prompting: the `PREREQUISITES` dict + the wave dispatcher (ADR-0002). The old prompt rule and the supervisor's force-queue fallback were deleted — don't reintroduce them.

**RAG goes through the standalone MCP server.** `rag_search_node` no longer touches the vector store directly: it calls the MCP tools `ingest_document` / `search_documents` via `agent/mcp_client.py` (ADR-0008/0010), so local and CI runs exercise the real MCP protocol. Transport is env-driven — `stdio` (a subprocess, the default) locally and in CI, `streamable-http` in deploy. The server itself lives in `mcp_server/server.py` and is deliberately document-generic (`entity` / `document_type`), not SEC-locked.

**Domain rules live in `agent/`, and `eval/` re-exports them — never the reverse.** Two rules are shared by the product and the measurement harness, so they sit in the product and the eval imports them: `agent/materiality.py` (the per-field tolerances — the eval asks "did the agent get this right?", the watchlist asks "is this change worth surfacing?", and they are the same question) and `agent/brief_parser.py` (reading figures back out of the Financial Snapshot table; `extract_all_periods` for the watchlist, `extract_period` for the eval). `eval/scoring.py` and `eval/run_eval.py` re-export them under their original names. If product code ever needs something from `eval/`, move it to `agent/` instead — the app must not depend on its test harness.

**Refreshes are gated by free Signals, and the worker runs outside the web service** (`agent/signals.py`, `watch_worker.py`, ADR-0011). A Signal is cheap and LLM-free — a new 10-K/10-Q/8-K for a CIK (`agent/sec_client.py`, uncached on purpose: a cached answer can only tell you what you already knew) or a price move. Only when one fires does a Refresh spend minutes and money. Two rules keep it honest: **`last_seen_accession` is updated on every sweep, refresh or not** (letting it drift would make a Signal fire forever), and **every sweep is recorded even when nothing changed** (`GET /sweeps`), so an empty change feed can be told apart from a dead cron. Signal sources that are down degrade to "no signal", never an exception. See `docs/watchlist-operations.md`.

**Deltas compare structured figures, not prose** (`agent/deltas.py`, ADR-0012). Every run stores `figures` — `{period: {field: value}}` parsed from the brief at save time — and a delta reports only changes that clear the materiality tolerance. Two invariants matter: a field only one run could read is **unknown**, never a change; and an empty delta is a correct, expected answer most days.

**Finished runs are persisted and permalinked.** `api/run_store.py` saves each completed run (brief *or* escalation) and `GET /runs/{id}` replays it; the frontend reads `?run=<id>`. Backend is env-selected like the vector store: Postgres when `PGVECTOR_URL` is set, SQLite under `.agent_runs/` otherwise. Saving is best-effort — `save_run` returns `None` on failure and the run still succeeds. Saved runs are readable by anyone with the (unguessable) link; there is no auth.

**Vector-store backend is auto-selected at import.** `rag/rag_backend.py`: if `PGVECTOR_URL` is set → `pgvector_store` (async, asyncpg); otherwise → `chroma_store` (local `.chroma_db/`, sync functions wrapped in `asyncio.to_thread`). Both expose the same async API (`ingest_chunks`, `query`, `format_rag_results`, `collection_stats`) so nothing downstream branches. The committed `.env` sets `PGVECTOR_URL`, so local runs use pgvector by default; unset it to fall back to ChromaDB with no Docker.

**State is a `TypedDict` with reducers for concurrent writes** (`agent/state.py`). `tool_results` and `tools_called` use the `append_list` reducer so parallel tool nodes can both append without `InvalidUpdateError`. Every tool node returns **deltas only** — just the result it produced (`[new]`); the dispatcher merges a wave and the reducer accumulates across waves and iterations. Do **not** return the full accumulated list (`state.get(...) + [new]`) from a node — it double-counts once the dispatcher merge and the reducer both apply (this was a real bug, issue #2).

**Tool nodes never raise.** Every node catches its own exceptions and returns a `ToolResult` with `success=False`. Success is detected downstream by a **string-prefix convention**: a tool "failed" iff its output starts with `"[<Tool> Error]"` (e.g. `[RAG Error]`, `[Calculator Error]`). Preserve that prefix format in any new tool.

**The `forecast` field bypasses the LLM.** `consensus_estimates_node` attaches a structured `forecast` dict (from `build_quarterly_outlook`) to state. The SSE endpoint emits it as its own `forecast` event *before* synthesis, and `frontend/index.html` charts it directly — the numbers never pass through Claude, so they can't be hallucinated. Synthesis prose and chart data are separate paths.

**Synthesis enforces citations and a fixed report structure** (`agent/nodes/synthesis.py`). The system prompt hard-codes the section order and a specific Financial Snapshot table (`FY2022…FY2027E` columns), mandates per-cell source tags (`[SEC Filing]`, `[Analyst Consensus — N analysts]`, etc.), injects today's date to prevent referencing future quarters, and forbids inventing figures. Two entry points share one prompt builder: `synthesis_node` (blocking, for CLI + batch API) and `stream_synthesis` (sync generator yielding tokens, bridged to async SSE via a `queue.Queue` + `asyncio.to_thread`).

**SSE streaming details** (`api/main.py`). `/research/stream` uses `graph.astream(stream_mode="values")` and reconstructs which node ran by diffing consecutive full-state snapshots (`_diff_state`: iteration count up → supervisor; `tool_results` grew → dispatcher/tool). A `Semaphore(3)` caps concurrent streams (returns 503 when full) to fit Railway's memory limit. Prefer SSE over WebSockets — traffic is strictly server→client.

## Notes

- Default model is `claude-opus-4-8`, read from `CLAUDE_MODEL` (both supervisor and synthesis). `MAX_ITERATIONS` defaults to 8.
- Six dispatcher tools (`web_search`, `wikipedia`, `sec_edgar`, `rag_search`, `arxiv`, `consensus_estimates`) plus the supervisor's **inline** `calculate_ratio` — which is *not* a dispatcher tool (issue #4). The README's "validated test cases" table still says "All 6" from older runs; treat those counts as historical.
- Deploys to Railway via `railway up` (see the git history / `.claude/settings.local.json`), not the Kubernetes path the README sketches.

## Agent skills

### Issue tracker

Issues and PRDs live in this repo's GitHub Issues (`ibs12/multi-tool-research-agent`), managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root (created lazily by `/domain-modeling`; none exist yet). See `docs/agents/domain.md`.
