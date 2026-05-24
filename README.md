# Multi-Tool Financial Research Agent

> An agentic AI system that autonomously researches any publicly traded company or market sector and generates a structured analyst brief — powered by Claude + LangGraph + pgvector.

Built as a demonstration of production-grade GenAI engineering for the Citi Junior GenAI Developer programme.

---

## Demo

```bash
# Stream node-by-node research in real time
python run.py --stream "Analyse JPMorgan Chase investment outlook"
```

```
🧠 supervisor  → queuing: ['web_search', 'wikipedia']
⚙️  dispatcher → web_search + wikipedia running concurrently
🧠 supervisor  → queuing: ['sec_edgar', 'rag_search']
⚙️  dispatcher → sec_edgar + rag_search running concurrently
                  └─ fetches 10-K from SEC, chunks it, embeds into pgvector
                  └─ semantic search returns real income statement figures
🧠 supervisor  → queuing: ['calculator']
⚙️  dispatcher → calculator: pe_ratio stock_price=234.5 eps=8.74 = 26.8x
🧠 supervisor  → ready_to_synthesise
✍️  synthesis  → Analyst brief with [SEC Filing] cited primary source data
```

---

## Quickstart

### Option A — Full stack with Docker Compose (recommended)

```bash
git clone https://github.com/yourusername/multi-tool-research-agent.git
cd multi-tool-research-agent

# Configure environment
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY and TAVILY_API_KEY

# Start everything: PostgreSQL/pgvector + API server
docker compose up -d

# Verify both services are healthy
docker compose ps

# Open the frontend
open http://localhost:8000

# Stream a research query
curl -N -X POST http://localhost:8000/research/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyse Apple Inc. investment outlook"}'
```

### Option B — Local development (pgvector in Docker, agent locally)

```bash
# Install dependencies
python -m venv myenv && source myenv/bin/activate
pip install -r requirements.txt

# Start only the database
docker compose up pgvector -d

# Configure environment
cp .env.example .env
# Edit .env — add your API keys

# Run the CLI agent
python run.py --stream "Analyse Apple Inc. investment outlook"

# Or start the API server
python server.py
# Open http://localhost:8000
```

---

## Architecture

```
                         ┌─────────────────────────────────┐
                         │          AgentState             │
                         │   TypedDict with Annotated      │
                         │   reducers for safe concurrent  │
                         │   writes from parallel nodes    │
                         └─────────────────────────────────┘
                                        │
        ┌───────────────────────────────┼──────────────────────────┐
        ▼                               ▼                          ▼
  ┌───────────┐              ┌──────────────────┐          ┌────────────┐
  │ Supervisor │              │   Async          │          │ Synthesis  │
  │  (Claude) │──tools_queue─▶   Dispatcher     │          │  (Claude)  │
  │           │◀─────────────│  asyncio.gather()│          │            │
  │ Native    │   results    │  ThreadPoolExec  │          │ Writes     │
  │ tool-use  │              └──────────────────┘          │ report     │
  │ API       │                       │                    └────────────┘
  └───────────┘              ┌────────┴────────────────────────────────┐
                             │              Tool Registry              │
                             │                                         │
                             │  web_search   wikipedia   sec_edgar     │
                             │  rag_search   calculator  arxiv         │
                             └─────────────────────────────────────────┘
                                                │
                                    ┌───────────┘
                                    ▼
                        ┌───────────────────────┐
                        │    RAG Pipeline        │
                        │                        │
                        │  SEC EDGAR API         │
                        │    → fetch 10-K HTML   │
                        │    → chunk on Item 7/8 │
                        │    → embed (MiniLM)    │
                        │    → store pgvector    │
                        │    → cosine search     │
                        └───────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|---|---|
| **Supervisor-only routing** | Only the supervisor decides when to stop — prevents race conditions in concurrent execution |
| **Native Anthropic tool-use API** | Typed `TOOL_SCHEMAS` — Claude returns structured `tool_use` blocks, no string parsing |
| **Async parallel dispatcher** | `asyncio.gather()` + `ThreadPoolExecutor` — web_search + wikipedia run concurrently, ~30% latency reduction |
| **pgvector on PostgreSQL** | ACID-compliant vector store — strict SQL `WHERE company = ?` prevents cross-company result contamination |
| **Section-boundary chunking** | Chunks split on SEC Item headers (Item 7 MD&A, Item 8 Financial Statements) — semantically coherent retrieval |
| **Annotated reducers on state** | `append_list` on `tool_results` and `tools_called` — safe concurrent writes without `InvalidUpdateError` |
| **Separate supervisor and synthesis** | Single responsibility: supervisor reasons about *what to do*; synthesis reasons about *what to say* |

---

## Tool Stack

| Tool | Source | Purpose |
|---|---|---|
| **Web Search** | Tavily API | Live news, earnings, analyst sentiment — financial domains only |
| **Wikipedia** | Wikipedia API | Stable company facts: founding, HQ, business model, segments |
| **SEC EDGAR** | EDGAR submissions API (no key) | Primary source 10-K/10-Q/8-K filing metadata |
| **RAG Search** | pgvector + sentence-transformers | Semantic search over actual 10-K/10-Q document text |
| **Calculator** | SymPy (sandboxed) | P/E, D/E, CAGR, revenue growth — safe, no `eval()` |
| **ArXiv** | ArXiv client | Academic research in q-fin, cs.AI, cs.LG categories |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent orchestration | LangGraph `StateGraph` |
| LLM + tool-use | Anthropic Claude (`claude-sonnet-4-6`) with native tool schemas |
| Async execution | `asyncio.gather()` + `ThreadPoolExecutor` |
| Vector database | **pgvector** on PostgreSQL 16 |
| RAG embeddings | `sentence-transformers/all-MiniLM-L6-v2` (local, no API cost) |
| RAG document fetch | SEC EDGAR HTML → section-boundary chunking |
| API layer | FastAPI + Server-Sent Events (SSE) |
| Frontend | Bloomberg Terminal-style HTML/JS |
| Containerisation | Docker + Docker Compose |
| Retry logic | Tenacity |
| Math engine | SymPy |
| CLI output | Rich |

---

## Project Structure

```
├── agent/
│   ├── state.py            # AgentState TypedDict with Annotated reducers
│   ├── graph.py            # StateGraph — nodes, edges, async dispatcher
│   └── nodes/
│       ├── supervisor.py   # Claude + native tool-use API — plans each iteration
│       ├── synthesis.py    # Claude — writes the final analyst brief
│       └── tools.py        # LangGraph nodes wrapping all 6 tool implementations
├── tools/
│   ├── web_search.py       # Tavily — financial domain filtering, retry logic
│   ├── wikipedia.py        # Wikipedia — lead section extraction, ticker stripping
│   ├── sec_edgar.py        # SEC EDGAR — submissions API, ticker→CIK→filings
│   ├── rag_search.py       # RAG pipeline — ingest filings, query pgvector
│   ├── calculator.py       # SymPy — safe expression eval + financial ratio library
│   └── arxiv_search.py     # ArXiv — q-fin + cs.AI category filtering
├── rag/
│   ├── pgvector_store.py   # pgvector backend — ingest, query, format
│   └── sec_fetcher.py      # Fetch SEC HTML, strip tags, chunk on section headers
├── api/
│   └── main.py             # FastAPI — batch endpoint + SSE streaming endpoint
├── frontend/
│   └── index.html          # Bloomberg Terminal-style UI — live SSE streaming
├── tests/
│   └── test_api.py         # Smoke tests for both API endpoints
├── Dockerfile              # Multi-stage Python 3.12 build
├── docker-compose.yml      # Full stack: pgvector + agent API
├── run.py                  # CLI entrypoint with Rich output + --stream flag
└── server.py               # Uvicorn server entrypoint
```

---

## Environment Configuration

Copy `.env.example` to `.env` and fill in your keys:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...        # console.anthropic.com
TAVILY_API_KEY=tvly-...             # tavily.com (free tier available)

# Database — matches docker-compose.yml defaults
PGVECTOR_URL=postgresql://postgres:ragpassword@localhost:5433/financial_agent
POSTGRES_PASSWORD=ragpassword
POSTGRES_DB=financial_agent
POSTGRES_USER=postgres
POSTGRES_PORT=5433

# Agent tuning
CLAUDE_MODEL=claude-sonnet-4-6
MAX_ITERATIONS=8
API_HOST=0.0.0.0
API_PORT=8000
```

> SEC EDGAR requires no API key — uses the public submissions API.
> The sentence-transformer model downloads automatically on first run.

---

## API Reference

### `POST /research` — Batch
```json
// Request
{"query": "Analyse Boeing investment outlook 2026", "max_iterations": 8}

// Response
{
  "query": "Analyse Boeing investment outlook 2026",
  "company_target": "Boeing Co. (BA)",
  "final_report": "## Executive Summary\n...",
  "tools_called": ["web_search", "wikipedia", "sec_edgar", "rag_search", "calculator", "arxiv"],
  "iteration_count": 4,
  "elapsed_seconds": 38.1,
  "error": null
}
```

### `POST /research/stream` — Server-Sent Events
```
data: {"event": "node_complete", "node": "supervisor",  "data": {"tools_queued": ["web_search", "wikipedia"], ...}}
data: {"event": "node_complete", "node": "dispatcher",  "data": {"tool_results": [...]}}
data: {"event": "node_complete", "node": "supervisor",  "data": {"tools_queued": ["sec_edgar", "rag_search"], ...}}
data: {"event": "node_complete", "node": "dispatcher",  "data": {"tool_results": [...]}}
data: {"event": "node_complete", "node": "synthesis",   "data": {"complete": true}}
data: {"event": "report",        "data": "## Executive Summary\n..."}
data: {"event": "done"}
```

### `GET /health`
```json
{"status": "ok", "service": "financial-research-agent"}
```

---

## RAG Pipeline Detail

The RAG layer extracts real financial figures from SEC filings:

```
1. sec_edgar tool    → finds filing URLs via EDGAR submissions API
                        (ticker → CIK → structured filing history)

2. rag_search tool   → fetches 10-K/10-Q HTML from SEC
                        → strips HTML, chunks on Item boundaries:
                           Item 1  Business
                           Item 1A Risk Factors
                           Item 7  MD&A          ← revenue, margins
                           Item 8  Financial Statements ← income statement
                        → embeds with all-MiniLM-L6-v2 (384-dim)
                        → upserts into pgvector (idempotent via MD5 hash)

3. cosine search     → SELECT ... ORDER BY embedding <=> query::vector
                        → strict WHERE company = ? filter
                        → returns top-5 passages by similarity

4. synthesis node    → cites passages as [SEC Filing] in the report
```

**pgvector SQL query:**
```sql
SELECT content, company, form_type, filed_at, section, source_url,
       1 - (embedding <=> %s::vector) AS similarity
FROM sec_filing_chunks
WHERE company = %s
ORDER BY embedding <=> %s::vector
LIMIT 5;
```

**Inspect the vector store directly:**
```bash
docker exec -it pgvector-financial psql -U postgres -d financial_agent \
  -c "SELECT company, form_type, filed_at, COUNT(*) FROM sec_filing_chunks GROUP BY 1,2,3 ORDER BY filed_at DESC;"
```

---

## Docker Compose Reference

```bash
# Start full stack
docker compose up -d

# Start database only (for local development)
docker compose up pgvector -d

# View logs
docker compose logs -f agent
docker compose logs -f pgvector

# Stop everything
docker compose down

# Stop and remove volumes (resets the vector store)
docker compose down -v

# Rebuild after code changes
docker compose up -d --build agent
```

---

## Responsible AI

| Principle | Implementation |
|---|---|
| **No hallucinated figures** | Synthesis prompt explicitly forbids inventing data — missing figures stated as unavailable |
| **Source citations** | Every claim tagged `[Web Search]`, `[Wikipedia]`, `[SEC Filing]`, `[Calculator]`, or `[ArXiv]` |
| **Primary source priority** | pgvector RAG over SEC filings cited before web search summaries |
| **No cross-company contamination** | pgvector uses strict SQL `WHERE company = ?` — never returns Apple chunks for a JPM query |
| **Sandboxed calculator** | SymPy parses expressions — no `eval()`. Regex allowlist rejects non-numeric input |
| **Iteration ceiling** | `max_iterations` prevents infinite loops — agent always terminates |
| **Graceful degradation** | Every node catches its own exceptions — graph never crashes on a single tool failure |

---

## Validated Test Cases

| Query | Tools | Notable Output |
|---|---|---|
| `Analyse Apple Inc. investment outlook` | All 6 | Real 10-K income statement: $143.8B revenue, 48.2% gross margin `[SEC Filing]` |
| `Analyse JPMorgan Chase investment outlook` | All 6 | Q4 2025 net income $57.5B, ROTCE 20%, NII guidance $95B |
| `Analyse Citi investment outlook` | All 6 | Transformation thesis, 33.8% EPS growth, Wikipedia segment detail |
| `Analyse Goldman Sachs investment risk profile` | All 6 | Flagged anomalous 66.5x P/E with analytical caveat |
| `Analyse Boeing investment outlook 2026` | All 6 | Pivoted to FCF valuation with negative EPS — correct behaviour |
| `Analyse HSBC investment outlook` | 4 (no EDGAR — UK listed) | Correctly noted EDGAR unavailable for non-US entity |
| `What are the investment risks in the US banking sector?` | All 6 | Sector query handled with ETF proxies (KBWB, XLF) |
| `Analyse Berkshire Hathaway` | 4 | Identified OxyChem acquisition and EPS contraction |

---

## Extending the Agent

### Add a new tool in 4 steps

1. Implement `run_mytool(query: str) -> str` in `tools/mytool.py`
2. Add a node function in `agent/nodes/tools.py`
3. Register it in `TOOL_REGISTRY` in `agent/graph.py`
4. Add it to `TOOL_SCHEMAS` enum in `agent/nodes/supervisor.py`

### Production deployment path

```
Local Docker Compose
       ↓
Kubernetes (OpenShift at Citi)
  - agent:     Deployment with HPA
  - pgvector:  Aurora PostgreSQL with pgvector extension
  - PGVECTOR_URL: points to managed RDS endpoint
  - API keys:  Kubernetes Secrets / AWS Secrets Manager
  - Monitoring: Splunk for supervisor reasoning logs, tool latencies
```

---

## Concepts Demonstrated

| Concept | File |
|---|---|
| LangGraph `StateGraph` with conditional edges | `agent/graph.py` |
| `TypedDict` + `Annotated` reducers for concurrent state | `agent/state.py` |
| ReAct agent pattern | `agent/nodes/supervisor.py` |
| Anthropic native tool-use API with typed schemas | `agent/nodes/supervisor.py` — `TOOL_SCHEMAS` |
| Async parallel execution (`asyncio.gather`) | `agent/graph.py` — `async_tool_dispatcher` |
| Sync-to-async bridge via `ThreadPoolExecutor` | `agent/graph.py` — `run_in_executor` |
| RAG pipeline over primary source documents | `rag/`, `tools/rag_search.py` |
| pgvector cosine similarity search with SQL filters | `rag/pgvector_store.py` |
| SEC section-boundary document chunking | `rag/sec_fetcher.py` |
| FastAPI SSE streaming endpoint | `api/main.py` |
| Multi-stage Docker build | `Dockerfile` |
| Full-stack Docker Compose | `docker-compose.yml` |
| Responsible AI — no hallucination | `agent/nodes/synthesis.py` |
| Safe sandboxed math evaluation | `tools/calculator.py` |