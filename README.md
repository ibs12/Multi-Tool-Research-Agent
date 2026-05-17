# Multi-Tool Financial Research Agent

> An agentic AI system that autonomously researches any publicly traded company or market sector and generates a structured analyst brief — powered by Claude + LangGraph.

Built as a demonstration of production-grade GenAI engineering for the Citi Junior GenAI Developer programme.

---

## What It Does

Given a natural language query, the agent:

1. **Plans** — Claude reasons about what information is needed and which tools to call
2. **Researches** — five specialised tools run in parallel, gathering live data from multiple sources
3. **Iterates** — the supervisor reviews results, identifies gaps, and queues further tool calls
4. **Synthesises** — Claude writes a structured analyst brief with cited sources and no fabricated figures
5. **Streams** — every node completion is streamed in real time over HTTP or CLI

```
Query: "Analyse Apple Inc. investment outlook"

🧠 supervisor  → queuing: ['web_search', 'wikipedia']         (parallel)
⚙️  dispatcher → web_search + wikipedia running concurrently
🧠 supervisor  → queuing: ['sec_edgar']
⚙️  dispatcher → sec_edgar running
🧠 supervisor  → queuing: ['calculator']                       (native tool-use)
⚙️  dispatcher → calculator running
🧠 supervisor  → queuing: ['arxiv']
⚙️  dispatcher → arxiv running
🧠 supervisor  → ready_to_synthesise: true
✍️  synthesis  → Analyst brief generated (5 tools, 5 iterations, ~35s)
```

**Sample output includes:**
- Executive Summary with investment stance (Bullish / Neutral / Bearish)
- Company Overview sourced from Wikipedia
- Recent Developments with live citations
- Financial Snapshot table with computed ratios
- Risk Factors grounded in real data
- Analyst Verdict with 12–24 month horizon

---

## Architecture

```
                    ┌─────────────────────────────┐
                    │        AgentState           │
                    │  (typed, shared memory)     │
                    │  messages, tool_results,    │
                    │  tools_remaining, plan...   │
                    └─────────────────────────────┘
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
   ┌───────────┐          ┌──────────────┐         ┌───────────┐
   │ Supervisor │          │   Dispatcher │         │ Synthesis │
   │  (Claude) │          │  (async)     │         │  (Claude) │
   │           │          │              │         │           │
   │ • Reasons │──tools──▶│ • Runs tools │         │ • Writes  │
   │ • Plans   │◀─results─│   in parallel│         │   report  │
   │ • Decides │          │ • asyncio    │         │ • Cites   │
   └───────────┘          │   .gather() │         │   sources │
         │                └──────────────┘         └───────────┘
         │                        │
         │              ┌─────────┴──────────────────────────┐
         │              │           Tool Registry             │
         │              │  ┌──────────┐  ┌───────────┐       │
         │              │  │web_search│  │ wikipedia │       │
         │              │  └──────────┘  └───────────┘       │
         │              │  ┌──────────┐  ┌───────────┐       │
         │              │  │sec_edgar │  │  arxiv    │       │
         │              │  └──────────┘  └───────────┘       │
         │              │  ┌──────────┐                       │
         │              │  │calculator│  (native tool-use)    │
         │              │  └──────────┘                       │
         │              └────────────────────────────────────┘
         │
    ready_to_synthesise?
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Supervisor-only routing** | Only the supervisor decides when to stop. Tools always return to supervisor, preventing race conditions. |
| **Native Anthropic tool-use API** | Supervisor uses typed `TOOL_SCHEMAS` — Claude returns structured `tool_use` blocks, eliminating string parsing for calculator calls. |
| **Async parallel dispatcher** | `asyncio.gather()` + `ThreadPoolExecutor` runs multiple tools concurrently. web_search + wikipedia in parallel saves ~30% latency vs sequential. |
| **`Annotated` reducers on state** | `append_list` reducer on `tool_results` and `tools_called` allows safe concurrent writes from parallel tool nodes without `InvalidUpdateError`. |
| **Separate supervisor and synthesis** | Single responsibility: supervisor reasons about *what to do*; synthesis reasons about *what to say*. Mixing them degrades both prompts. |
| **ReAct pattern** | Each iteration: Reason (plan) → Act (tool call) → Observe (result) → repeat. LangGraph's stateful loop makes multi-step ReAct practical. |

---

## Tool Stack

| Tool | Source | Purpose |
|---|---|---|
| **Web Search** | Tavily API | Live news, earnings, analyst sentiment — financial domains only |
| **Wikipedia** | Wikipedia API | Stable company facts: founding, HQ, business model, segments |
| **SEC EDGAR** | EDGAR full-text search (no key) | Primary source 10-K/10-Q/8-K filings — verified financial data |
| **Calculator** | SymPy (sandboxed) | P/E, D/E, CAGR, revenue growth, profit margin — safe, no `eval()` |
| **ArXiv** | ArXiv client | Academic research in q-fin, cs.AI, cs.LG categories |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph `StateGraph` |
| LLM | Anthropic Claude (`claude-sonnet-4-6`) |
| Tool-use | Anthropic native tool-use API (typed schemas) |
| Async execution | `asyncio.gather()` + `ThreadPoolExecutor` |
| API layer | FastAPI + Server-Sent Events (SSE) |
| CLI output | Rich |
| Retry logic | Tenacity |
| Math engine | SymPy |

---

## Project Structure

```
├── agent/
│   ├── state.py            # AgentState TypedDict — shared memory with Annotated reducers
│   ├── graph.py            # StateGraph — nodes, edges, async dispatcher
│   └── nodes/
│       ├── supervisor.py   # Claude + native tool-use API — plans each iteration
│       ├── synthesis.py    # Claude — writes the final analyst brief
│       └── tools.py        # LangGraph nodes wrapping all 5 tool implementations
├── tools/
│   ├── web_search.py       # Tavily — financial domain filtering, retry logic
│   ├── wikipedia.py        # Wikipedia — lead section extraction, ticker stripping
│   ├── sec_edgar.py        # SEC EDGAR — 10-K/10-Q/8-K primary source filings
│   ├── calculator.py       # SymPy — safe expression eval + financial ratio library
│   └── arxiv_search.py     # ArXiv — q-fin + cs.AI category filtering
├── api/
│   └── main.py             # FastAPI — batch endpoint + SSE streaming endpoint
├── tests/
│   └── test_api.py         # Smoke tests for both API endpoints
├── run.py                  # CLI entrypoint with Rich output + --stream flag
└── server.py               # Uvicorn server entrypoint
```

---

## Quickstart

### 1. Clone and install

```bash
git clone https://github.com/yourusername/multi-tool-research-agent.git
cd multi-tool-research-agent
python -m venv myenv && source myenv/bin/activate  # Windows: myenv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```env
ANTHROPIC_API_KEY=sk-ant-...        # console.anthropic.com
TAVILY_API_KEY=tvly-...             # tavily.com (free tier available)
CLAUDE_MODEL=claude-sonnet-4-6
MAX_ITERATIONS=8
API_HOST=0.0.0.0
API_PORT=8000
```

> SEC EDGAR requires no API key — it uses the public EDGAR full-text search API.

### 3. Run the CLI

```bash
# Standard mode
python run.py "Analyse JPMorgan Chase investment outlook"

# Streaming mode — see each node fire in real time (recommended for demos)
python run.py --stream "Analyse Apple Inc. investment outlook"
```

### 4. Run the API server

```bash
python server.py
```

Open `http://localhost:8000/docs` for the interactive Swagger UI.

### 5. Test the API

```bash
# Terminal 1 — start server
python server.py

# Terminal 2 — batch request
curl -X POST http://localhost:8000/research \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyse Citi investment outlook"}' | python -m json.tool

# Terminal 2 — SSE stream
curl -N -X POST http://localhost:8000/research/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyse Goldman Sachs investment risk profile"}'

# Terminal 2 — automated smoke tests
python tests/test_api.py
```

---

## API Reference

### `POST /research` — Batch
Blocks until the full report is ready.

```json
// Request
{
  "query": "Analyse Boeing investment outlook 2026",
  "max_iterations": 8
}

// Response
{
  "query": "Analyse Boeing investment outlook 2026",
  "company_target": "Boeing Co. (BA)",
  "final_report": "## Executive Summary\n...",
  "tools_called": ["web_search", "wikipedia", "sec_edgar", "calculator", "arxiv"],
  "iteration_count": 5,
  "elapsed_seconds": 34.2,
  "error": null
}
```

### `POST /research/stream` — Server-Sent Events
Streams node completions as they happen. Connect with `EventSource` in the browser or `curl -N` in the terminal.

```
data: {"event": "node_complete", "node": "supervisor",  "data": {"tools_queued": ["web_search", "wikipedia"], ...}}
data: {"event": "node_complete", "node": "dispatcher",  "data": {...}}
data: {"event": "node_complete", "node": "supervisor",  "data": {"tools_queued": ["sec_edgar"], ...}}
data: {"event": "node_complete", "node": "dispatcher",  "data": {...}}
data: {"event": "node_complete", "node": "synthesis",   "data": {"complete": true}}
data: {"event": "report",        "data": "## Executive Summary\n..."}
data: {"event": "done"}
```

### `GET /health`
```json
{"status": "ok", "service": "financial-research-agent"}
```

---

## Responsible AI

Responsible AI principles are embedded in the implementation, not bolted on:

| Principle | Implementation |
|---|---|
| **No hallucinated figures** | Synthesis prompt explicitly forbids inventing data. Missing figures are stated as unavailable with a recommended alternative source. |
| **Source citations** | Every claim is tagged `[Web Search]`, `[Wikipedia]`, `[SEC EDGAR]`, `[Calculator]`, or `[ArXiv]`. |
| **Primary source priority** | SEC EDGAR filings are flagged as authoritative over web search summaries in the synthesis prompt. |
| **Sandboxed calculator** | SymPy parses expressions — no `eval()`. A regex allowlist rejects non-numeric input before parsing. |
| **Iteration ceiling** | `max_iterations` prevents infinite loops. The agent always terminates. |
| **Graceful degradation** | Every node catches its own exceptions and returns a structured error. The graph never crashes on a single tool failure. |
| **Balanced-brace JSON parsing** | Supervisor response parsing uses character-level brace tracking rather than greedy regex, preventing crashes when Claude adds trailing text. |

---

## Validated Test Cases

The agent has been tested across a range of query types:

| Query | Tools Used | Notable Behaviour |
|---|---|---|
| `Analyse JPMorgan Chase investment outlook` | All 5 | Computed P/E from live EPS data |
| `Analyse Apple Inc. investment outlook` | All 5 | 34.1x forward P/E, 2026/2027 estimates |
| `Analyse Citi investment outlook` | All 5 | Identified transformation thesis, 33.8% EPS growth |
| `Analyse Goldman Sachs investment risk profile` | All 5 | Flagged anomalous 66.5x P/E with analytical note |
| `Analyse Boeing investment outlook 2026` | All 5 | Pivoted to FCF valuation with negative EPS |
| `Analyse HSBC investment outlook` | 4 (no EDGAR — UK listed) | Correctly noted EDGAR unavailability for non-US entity |
| `What are the investment risks in the US banking sector?` | All 5 | Handled sector query, used ETF proxies |
| `Analyse Berkshire Hathaway` | 3 | Identified leadership transition and EPS contraction |

---

## Extending the Agent

### Add a new tool in 4 steps

1. Implement `run_mytool(query: str) -> str` in `tools/mytool.py`
2. Add a node function in `agent/nodes/tools.py`
3. Register it in `TOOL_REGISTRY` in `agent/graph.py`
4. Add it to `TOOL_SCHEMAS` in `agent/nodes/supervisor.py`

### Ideas for further development

- **RAG over SEC filings** — embed 10-K sections into pgvector for semantic retrieval of specific financial line items
- **Earnings call transcripts** — integrate a transcript API (e.g. Motley Fool, Seeking Alpha) for management commentary
- **Portfolio mode** — accept a list of tickers and generate a comparative sector brief
- **Persistent memory** — store past reports in PostgreSQL so the agent can reference previous research on the same company
- **MLOps monitoring** — log supervisor reasoning, tool latency, and synthesis quality to Splunk or Datadog

---

## Concepts Demonstrated

| Concept | File |
|---|---|
| LangGraph `StateGraph` with conditional edges | `agent/graph.py` |
| `TypedDict` + `Annotated` reducers for concurrent state | `agent/state.py` |
| ReAct agent pattern | `agent/nodes/supervisor.py` |
| Anthropic native tool-use API with typed schemas | `agent/nodes/supervisor.py` — `TOOL_SCHEMAS` |
| Async parallel execution with `asyncio.gather()` | `agent/graph.py` — `async_tool_dispatcher` |
| Sync-to-async bridge via `ThreadPoolExecutor` | `agent/graph.py` — `run_in_executor` |
| FastAPI SSE streaming endpoint | `api/main.py` |
| Primary source financial data (SEC EDGAR) | `tools/sec_edgar.py` |
| Safe sandboxed math evaluation | `tools/calculator.py` — SymPy + regex allowlist |
| Graceful degradation and error recovery | All tool nodes |
| Responsible AI / no hallucination | `agent/nodes/synthesis.py` |