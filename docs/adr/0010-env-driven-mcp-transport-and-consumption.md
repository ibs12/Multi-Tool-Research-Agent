# Agents consume the MCP server over one env-driven client; transport is stdio locally/CI, HTTP in deploy

The RAG-MCP server ([ADR-0008](./0008-generic-document-rag-mcp-server.md)) is consumed by the agents through [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)' `MultiServerMCPClient`. Two consumption questions: how the client is wired, and which transport each environment uses.

## Client wiring

- **One client, instantiated once** (module scope in `agent/mcp_client.py`). `get_tools()` is fetched once and the tools bound to agents. Tools are static, so per-request client construction is pure overhead; each tool call opens its own transport session internally.
- **Per-agent tool binding = the [ADR-0007](./0007-one-shared-state-nested-handoff-contract.md) roster restated as wiring:** the research agent binds `ingest_document` + `search_documents`; risk-analyst and compliance-checker bind `search_documents` only (they read evidence, they don't ingest).
- **Pinned + wrapped.** `langchain-mcp-adapters` is pinned (it is pre-1.0 and its surface still moves) and all construction lives behind `agent/mcp_client.py`, so the future `langchain.mcp` (`MCPAdapter`) migration is a one-file change — the wiring *shape* is stable even though the import path will move.
- The former `rag_search` node is removed: the research loop binds the MCP tools directly, and the `PREREQUISITES` edge simply renames `rag_search ← sec_edgar` → `ingest_document ← sec_edgar` — the same self-heal dispatcher ([ADR-0002](./0002-tool-ordering-is-structural-not-prompted.md)), not a new mechanism.

## Transport per environment

The `MultiServerMCPClient` connection is chosen by env var — `stdio` (server run as a subprocess) locally and in CI/eval; `streamable-http` (a Railway URL) in deploy — mirroring how `PGVECTOR_URL` already selects pgvector-vs-Chroma ([ADR-0005](./0005-dual-vector-store-backend.md)). Agents *always* go through the MCP client, so local and CI runs exercise the **real MCP protocol path**.

## Considered options (transport)

- **Env-driven, uniform MCP path (chosen).** `stdio`-in-CI drops the live service *and* the database: with no `PGVECTOR_URL`, the server falls back to embedded Chroma, so the Map 2 eval-gate runs the real MCP protocol (real schemas, real JSON-RPC round-trips) against an embedded-store server with **zero external provisioning** — cheap enough for a per-PR GitHub Actions gate rather than a slow/flaky job people learn to ignore.
- **HTTP everywhere.** Rejected: one code path is marginally simpler, but it makes every CI run depend on a live networked service starting → healthy → clean teardown — a more common flaky-CI source than a conditional transport selection.
- **Bypass MCP in tests.** Rejected: it would exercise a different code path than production, so a green test would prove nothing about the shipped path.

## Consequences

- The zero-external-deps CI path depends on the Chroma fallback being installable, so `chromadb` is now a pinned requirement.
- Same-batch RAG-MCP hazards between risk-analyst and compliance-checker outputs will be handled by adding `PREREQUISITES` entries when those calls are built ([ADR-0007](./0007-one-shared-state-nested-handoff-contract.md) forward note).
