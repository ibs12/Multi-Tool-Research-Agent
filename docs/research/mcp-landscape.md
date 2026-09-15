# MCP Landscape for the Standalone RAG Server

**Ticket:** MCP1 (#19), Map 1 (#15) — research only, no application code changed.
**Date:** 2026-09-14. **Scope:** decide the defaults that MCP2 (server boundary & tool
contract) and MCP3 (client wiring) will build on.

## Why this document exists

We are extracting this project's RAG capability out of the single LangGraph and into a
**standalone MCP server** that several LangGraph agents (a supervisor, a risk-analyst, a
compliance-checker) can call. Today the capability lives in-process:

- `rag/rag_backend.py` exposes an async API — `ingest_chunks`, `query`,
  `format_rag_results`, `collection_stats` — and auto-selects the backend at import time
  (`PGVECTOR_URL` set → Postgres/pgvector via `asyncpg`; unset → ChromaDB wrapped in
  `asyncio.to_thread`).
- `tools/rag_search.py` wraps that into three entry points — `ingest_filings_from_state`,
  `run_rag_query(query_text, company)`, and `run_rag_pipeline(query_text, tool_results,
  company)` — and is invoked directly by `agent/nodes/tools.py::rag_search_node`.

Fixed constraints that drive the decisions below: **Python 3.12**, **LangGraph**, **deploys
to Railway** (Docker; `server.py` already runs FastAPI/uvicorn on a port), and a standing
**legibility-first / minimal-complexity** principle (ADR 0001,
"optimize for legibility over production hardening"). This is a job-search portfolio
artifact, not a high-scale product — so the tie-breaker throughout is "fewest moving parts
a reviewer has to hold in their head," not throughput or resilience.

---

## Axis 1 — Transport: stdio vs Streamable HTTP (vs the old HTTP+SSE)

### The options (MCP spec, revision 2025-06-18)

The current spec defines **two** standard transports, and clients "SHOULD support stdio
whenever possible."[^spec-transports]

- **stdio** — the client *launches the MCP server as a subprocess* and exchanges
  newline-delimited JSON-RPC over the server's `stdin`/`stdout` (`stderr` is free for
  logs). One server process per client; no network, no ports, no auth story because
  nothing is exposed.[^spec-transports]
- **Streamable HTTP** — the server "operates as an independent process that can handle
  multiple client connections," over a *single* MCP endpoint (e.g. `https://host/mcp`)
  that accepts HTTP **POST** (client→server messages) and **GET** (open an SSE stream for
  server→client messages). A request can be answered either as one JSON response or as an
  SSE stream. Optional session management via the `Mcp-Session-Id` header, plus stream
  resumability via `Last-Event-ID`.[^spec-transports]
- **HTTP+SSE (2024-11-05)** — the *older* two-endpoint transport. The spec states plainly
  that Streamable HTTP "replaces the HTTP+SSE transport from protocol version
  2024-11-05," and HTTP+SSE is referred to as **deprecated**.[^spec-transports] Treat it
  as legacy; only relevant if we had to talk to an old server, which we don't.

Security note the spec is explicit about for Streamable HTTP: servers **MUST** validate
the `Origin` header (DNS-rebinding defense), **SHOULD** bind to localhost when running
locally, and **SHOULD** authenticate all connections.[^spec-transports] stdio sidesteps
all three by never being on the network.

### Trade-offs for *this* server

| | stdio | Streamable HTTP |
|---|---|---|
| Deployment shape | co-located subprocess; caller spawns it | independent, separately-deployed web service |
| Multiple agents calling it | one process per client — each agent spawns its own | many concurrent clients on one server |
| Railway fit | ✗ — Railway runs a networked service, not a subprocess of your agents | ✓ — Railway exposes HTTP/HTTPS with automatic SSL + a generated domain[^railway-net] |
| Auth / exposure | none needed (not networked) | needs Origin validation + auth (spec SHOULD)[^spec-transports] |
| Local dev / tests | ✓ — zero network setup, deterministic | works, but needs a running server + port |

The research question — "which fits a networked, separately-deployed server vs a local dev
loop?" — answers itself against the spec's own framing: stdio is the *local subprocess*
model, Streamable HTTP is the *independent multi-client server* model.[^spec-transports]
Our target ("standalone, Railway-deployable, callable by three separate LangGraph agents")
is exactly the second.

### Recommended default → **Streamable HTTP for the deployed server; keep stdio for local dev/tests**

Three separately-deployed LangGraph agents cannot each subprocess-spawn a shared,
network-hosted RAG server — that is the multi-client HTTP case by construction, and
Streamable HTTP is the current-spec standard for it.[^spec-transports] On Railway it maps
directly onto the platform's HTTP/HTTPS public networking (auto-SSL, generated
`.railway.app` domain).[^railway-net] **Do not build on HTTP+SSE** — it's deprecated in
favour of Streamable HTTP.[^spec-transports] stdio stays valuable as a *second, free*
transport: the same FastMCP server object runs over stdio for local runs and unit tests
(no port, no auth, fully deterministic), which keeps the legibility-first dev loop cheap.
This dual-transport posture costs nothing extra — it is one `transport=` argument at
startup (Axis 3).

---

## Axis 2 — Client integration: how MCP tools reach a LangGraph node

Three ways to surface the RAG server's tools to the supervisor / risk-analyst /
compliance-checker agents.

### Option A — `langchain-mcp-adapters` → LangChain/LangGraph tools *(idiomatic)*

`MultiServerMCPClient` connects to one or more MCP servers (transport chosen per server:
`stdio`, `streamable_http`, or `sse`) and `get_tools()` / `load_mcp_tools` converts each
MCP tool into a **native LangChain `BaseTool`**.[^lc-adapters] Those tools are
indistinguishable from any other LangChain tool, so each LangGraph agent binds them the
normal way (`create_react_agent(model, tools)` / a `ToolNode`). Minimal shape:[^lc-adapters]

```python
client = MultiServerMCPClient({
    "rag": {"url": "https://<railway-domain>/mcp", "transport": "streamable_http"},
})
tools = await client.get_tools()          # list[BaseTool]
agent = create_react_agent(model, tools)  # each of the 3 agents binds normally
```

**2026 status / version churn (the ticket asked to flag this):** MCP support is moving
*into the main LangChain package* under the `langchain.mcp` namespace (install
`langchain[mcp]`, LangChain v1.x, currently **beta**), where `MultiServerMCPClient`
collapses into a single `MCPAdapter` class; the change tracks the July-2025 spec
rewrite.[^lc-blog][^lc-search] The standalone `langchain-mcp-adapters` repo still works and
is the most heavily documented path, but is now effectively in maintenance mode as that
migration lands.[^lc-search] Both produce the same thing: **plain LangChain tools**. So the
*shape* of MCP3's wiring is stable even though the import path is in flux.

### Option B — Anthropic Messages API MCP connector (`mcp_servers` + `mcp_toolset`)

Claude's Messages API can connect to a **remote** MCP server itself, server-side: you pass
`mcp_servers=[{"type":"url","url":...,"name":...}]` **and** a matching
`tools=[{"type":"mcp_toolset","mcp_server_name":...}]`, under beta header
`mcp-client-2025-11-20`; Anthropic makes the MCP connection and runs the tool loop — there
is no client-side tool execution.[^claude-mcp][^claude-skill] Both parameters are required
together (a server with no referencing `mcp_toolset` is a validation error), and the
connector is **URL/Streamable-HTTP only** — there is no stdio path.[^claude-mcp]

The catch for us: this bypasses LangGraph's tool orchestration entirely. The tools never
become LangChain `BaseTool`s and never pass through a `ToolNode` — they only exist inside a
single `client.beta.messages.create(...)` call. It fits a node that *calls Claude
directly*, not the graph's own tool-dispatch model. It's also Anthropic-model-specific.

### Option C — Raw MCP client (official `mcp` SDK)

Drive `ClientSession` over `stdio_client` / `streamablehttp_client` yourself, call
`list_tools()` / `call_tool()`, and hand-convert results into whatever the node
needs.[^py-sdk] Maximum control, maximum boilerplate — you re-implement exactly what
Option A already gives you (session lifecycle, schema translation, tool objects). Not
idiomatic for LangGraph.

### Recommended default → **Option A (`langchain-mcp-adapters`, converging to `langchain.mcp`)**

It is the only option that surfaces the RAG tools *as LangGraph-native tools* that all
three agents bind identically, which is precisely what MCP3 needs and what keeps the wiring
legible (one client, `get_tools()`, done).[^lc-adapters] Keep **Option B in reserve** for
the narrow case where a node talks to Claude's Messages API directly and we'd rather
Anthropic run the tool loop — but note it's remote-HTTP-only and steps outside LangGraph's
tool model.[^claude-mcp] **Avoid Option C** unless we hit something the adapter can't
express; hand-rolling the client is exactly the kind of complexity ADR 0001 tells us to
skip. Pin the LangChain MCP dependency and revisit when `langchain.mcp`/`MCPAdapter` exits
beta.[^lc-blog]

---

## Axis 3 — Server framework: FastMCP vs the official MCP Python SDK

These are **not really rivals** — FastMCP's high-level API *is* the official SDK's
high-level API. FastMCP 1.0 was incorporated into the official MCP Python SDK in
2024;[^fastmcp-welcome] the standalone **FastMCP 2.0** (`pip install fastmcp`,
`from fastmcp import FastMCP`) is the actively-developed superset that adds auth,
deployment helpers, client/testing utilities, etc.[^fastmcp-welcome] The bundled version
lives at `from mcp.server.fastmcp import FastMCP` in the official SDK.[^py-sdk] Either way
you write the same decorator code.

### Exposing the existing RAG service

Both use a `@mcp.tool` decorator that **auto-derives the JSON Schema from Python type hints
and the docstring** — no hand-written schema.[^fastmcp-welcome][^py-sdk] Our RAG entry
points are already async with clean, typed signatures and docstrings, so the server is a
thin adapter over them, e.g.:

```python
from fastmcp import FastMCP           # or: from mcp.server.fastmcp import FastMCP
from tools.rag_search import run_rag_query   # existing async fn
mcp = FastMCP(name="rag")

@mcp.tool
async def rag_query(query_text: str, company: str | None = None) -> str:
    """Semantic search over ingested SEC-filing chunks; returns cited passages."""
    return await run_rag_query(query_text, company)

if __name__ == "__main__":
    import os
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ["PORT"]))
```

### Transports & deployment

Both frameworks support **stdio (default)**, **HTTP/Streamable HTTP (recommended for
production / remote, multi-client)**, and **SSE (legacy — use HTTP for new
projects)**.[^fastmcp-run][^py-sdk] Running HTTP is a one-liner —
`mcp.run(transport="http", host="0.0.0.0", port=...)`, which serves the endpoint at
`/mcp`.[^fastmcp-run] For Railway that means: **bind `0.0.0.0` on the injected `PORT`**
(Railway routes its HTTPS edge to the container; auto-SSL + generated domain are provided
for you).[^railway-net] The same server object over `mcp.run()` (no args) is the stdio dev
loop from Axis 1.[^fastmcp-run]

### Recommended default → **FastMCP** (start with the official SDK's bundled `mcp.server.fastmcp`; reach for FastMCP 2.0 if we need its extras)

Decorator-per-tool is the least code and the most legible way to expose the existing async
RAG functions, and both flavours are the *same* high-level API.[^fastmcp-welcome][^py-sdk]
Start with the **official MCP Python SDK** (`from mcp.server.fastmcp import FastMCP`) —
one dependency, no extra library, canonical — and only adopt standalone **FastMCP 2.0** if
we later want its built-in auth or deployment helpers.[^fastmcp-welcome] Run Streamable HTTP
on Railway, stdio locally. This keeps the server boundary a thin, typed wrapper — exactly
the seam MCP2 will formalize.

---

## Facts MCP2 & MCP3 depend on

**For MCP2 (server boundary & tool contract):**

- **Tool surface mirrors the existing seam.** The natural boundary is the two-phase RAG
  operation already in the code: an **ingest** tool and a **query/search** tool (the
  combined `run_rag_pipeline` is a third candidate but couples ingest+query and reaches
  into agent `state`, so it's a weaker public contract). Map to `rag_backend`'s
  `ingest_chunks` / `query` / `collection_stats`.
- **Schemas are auto-generated from Python type hints + docstrings** under FastMCP — so
  the tool contract *is* the function signature. Keep parameters typed and docstrings
  crisp; that's the whole schema.[^fastmcp-welcome][^py-sdk] Decide explicitly whether
  tools return **structured content** or today's pre-formatted string
  (`format_rag_results` currently returns a display string, not structured data — fine for
  an LLM to read, but a structured return is more reusable across the 3 agents).
- **Backend selection stays server-side.** The `PGVECTOR_URL`→pgvector / else→Chroma switch
  in `rag_backend.py` should remain *inside* the server; clients must not know or care.
  This is the encapsulation win of the extraction.
- **Keep tools stateless per call** (query in → cited results out). Streamable HTTP has
  optional session state (`Mcp-Session-Id`), but the RAG query path doesn't need it, and
  stateless tools are simpler to reason about and to run behind Railway's edge.[^spec-transports]
- **Transport = Streamable HTTP in prod, stdio in tests** — same server object, one
  `transport=` argument (Axis 1/3).

**For MCP3 (client wiring):**

- Tools arrive as **LangChain `BaseTool`s** via
  `MultiServerMCPClient({"rag": {"url": "https://<railway-domain>/mcp", "transport":
  "streamable_http"}})` → `await client.get_tools()`; each agent binds them
  normally.[^lc-adapters] Import path is migrating to `langchain.mcp` / `MCPAdapter`
  (beta) — pin the dependency and treat the class name as the one volatile
  detail.[^lc-blog][^lc-search]
- **Streamable HTTP endpoint path is `/<mount>/mcp`** (default `/mcp`); the client needs
  the full URL including that path.[^fastmcp-run][^spec-transports]
- **Auth is a SHOULD, not automatic.** The spec says HTTP MCP servers SHOULD authenticate
  and MUST validate `Origin`.[^spec-transports] `MultiServerMCPClient` supports per-server
  `headers` (e.g. a bearer token), so the minimal-complexity path is a shared secret in a
  header — decide in MCP3 whether the portfolio scope even needs it, but the hook
  exists.[^lc-adapters]
- **The Anthropic MCP connector is the fallback wiring**, not the default: it's remote-URL
  only, requires the `mcp-client-2025-11-20` beta header plus paired `mcp_servers` +
  `mcp_toolset`, and keeps the tool loop inside a single Messages API call rather than in
  LangGraph.[^claude-mcp][^claude-skill]

---

## One-line summary per axis

1. **Transport → Streamable HTTP** (deployed, multi-client, Railway-native, current spec) with **stdio kept for local dev/tests**; HTTP+SSE is deprecated — don't use it.
2. **Client → `langchain-mcp-adapters` (`MultiServerMCPClient`, converging to `langchain.mcp`/`MCPAdapter`)** — it's the only option that hands each LangGraph agent plain LangChain tools; Anthropic's MCP connector is a direct-to-Claude fallback, raw MCP client is over-engineering.
3. **Server → FastMCP** (start with the official SDK's bundled `mcp.server.fastmcp.FastMCP`, upgrade to FastMCP 2.0 only for its extras) — a decorator-thin, typed wrapper over the existing async RAG functions, run over Streamable HTTP on Railway.

---

## Sources

[^spec-transports]: MCP specification, revision **2025-06-18**, "Transports" — two standard
    transports (stdio, Streamable HTTP); "Clients SHOULD support stdio whenever possible";
    stdio subprocess model; Streamable HTTP single-endpoint POST+GET with optional SSE,
    session management, and resumability; Streamable HTTP "replaces the HTTP+SSE transport
    from protocol version 2024-11-05" (HTTP+SSE deprecated); security requirements (validate
    `Origin`, bind localhost when local, authenticate).
    <https://modelcontextprotocol.io/specification/2025-06-18/basic/transports>
[^railway-net]: Railway Docs, "Public Networking" — Railway exposes services over HTTP/HTTPS
    with automatic SSL and a generated `.railway.app` domain (plus custom domains). (Standard
    Railway requirement: listen on `0.0.0.0` on the injected `PORT`.)
    <https://docs.railway.com/networking/public-networking>
[^lc-adapters]: `langchain-ai/langchain-mcp-adapters` (GitHub) — lightweight wrapper making
    MCP tools compatible with LangChain/LangGraph; `MultiServerMCPClient` with per-server
    transport config (`stdio`, `streamable_http`/`http`, `sse`) and optional headers;
    `get_tools()` / `load_mcp_tools` produce LangChain tools for `create_react_agent`.
    <https://github.com/langchain-ai/langchain-mcp-adapters>
[^lc-blog]: LangChain blog, "MCP in LangChain: Stateless Protocol, Elicitation, and More" —
    MCP support moved into the main package under `langchain.mcp` (install `langchain[mcp]`,
    v1.x, beta); `MultiServerMCPClient` collapses into a single `MCPAdapter` class; transports
    streamable HTTP / stdio / in-memory.
    <https://www.langchain.com/blog/mcp-in-langchain-stateless-protocol-elicitation-and-more>
[^lc-search]: LangChain reference/changelog (2026) corroborating the `langchain.mcp` /
    `MCPAdapter` migration and that the standalone `langchain-mcp-adapters` package is now in
    maintenance mode. <https://docs.langchain.com/oss/python/releases/changelog> ·
    <https://reference.langchain.com/python/langchain-mcp-adapters>
[^claude-mcp]: Anthropic MCP connector (Messages API) — `mcp_servers` (`{type:"url", url,
    name}`) **plus** a matching `tools:[{type:"mcp_toolset", mcp_server_name}]`, beta header
    `mcp-client-2025-11-20`; Anthropic makes the MCP connection server-side (no client-side
    tool loop); URL / Streamable HTTP only. Per the local `claude-api` skill (MCP Connector
    reference) and <https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>.
[^claude-skill]: Local `claude-api` skill — authoritative reference for the Messages-API MCP
    connector shape (`mcp_servers` + `mcp_toolset`, `mcp-client-2025-11-20`) and current
    model/API facts; invoked per the MCP1 ticket instructions.
[^py-sdk]: `modelcontextprotocol/python-sdk` (official MCP Python SDK, GitHub) — build servers
    with `FastMCP` and `@mcp.tool()` (auto schema from type hints); supports stdio,
    streamable-http, and sse; run streamable HTTP via
    `mcp run server.py --transport streamable-http` (endpoint `http://localhost:8000/mcp`).
    FastMCP 1.0's high-level API is bundled here. <https://github.com/modelcontextprotocol/python-sdk>
[^fastmcp-welcome]: FastMCP docs, "Welcome" — FastMCP is a full MCP framework; its high-level
    Python API was incorporated into the official MCP Python SDK in 2024; `@mcp.tool` derives
    schema/validation automatically; FastMCP 2.0 is the actively-developed superset.
    <https://gofastmcp.com/getting-started/welcome>
[^fastmcp-run]: FastMCP docs, "Running a FastMCP Server" — transports: STDIO (default),
    HTTP/Streamable HTTP (recommended for production/remote, multi-client), SSE (legacy);
    `mcp.run()` for stdio, `mcp.run(transport="http", host=..., port=...)` for HTTP (endpoint
    at `/mcp`). <https://gofastmcp.com/deployment/running-server>
