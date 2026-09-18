# The RAG capability is a generic document-RAG MCP server, not SEC-locked

RAG is extracted from an in-graph tool into a standalone MCP server callable by any agent. The boundary question: does the server model SEC filings specifically (`ingest_filing` / `search_filings`), or documents generically (`ingest_document` / `search_documents`)?

## Considered options

- **SEC-scoped server (B2)** — filing-shaped tools; optionally folds SEC discovery (ticker→CIK→URLs) into the server. Rejected (see below).
- **Generic document-RAG server (B1, chosen)** — document-shaped tools; SEC *discovery* stays a graph tool.

## Tool contract

```
ingest_document(url, entity, document_type, published_at) -> {chunks_ingested, ...}
search_documents(query, entity=None, document_type=None, top_k=5)
    -> list[{text, similarity, entity, document_type, published_at, section, source_url}]
collection_stats() -> {...}
```

`search_documents` returns **structured per-chunk results**, not the `format_rag_results` display string — the caller (an agent) composes citations from the fields. The store is server-side and encapsulated; the dual pgvector/Chroma backend ([ADR-0005](./0005-dual-vector-store-backend.md)) is kept — agents never touch the store, only the tools.

## Why generic over SEC-scoped

- **The "removes the `sec_edgar → rag` ordering hazard" argument for folding in discovery does not hold** — that hazard is *already* fixed by `PREREQUISITES` + the self-heal dispatcher ([ADR-0002](./0002-tool-ordering-is-structural-not-prompted.md)), a tested ~3-line mechanism. Folding discovery into the server would trade an already-cheap graph edge for a server coupling two unrelated systems (the SEC EDGAR HTTP API **and** a vector store).
- **Demonstrated reuse, not speculative genericity.** A second production project (**Lumen**) already does RAG over a different document type (news). `ingest_document`/`search_documents` cost no extra params over the SEC-named versions but back both projects. Speculative genericity is flexibility for a use case you don't have; this one exists.
- **MCP boundary convention.** MCP servers are conventionally scoped to one external system (GitHub, filesystem, Slack). A document-RAG capability keeps that shape; a server that is *both* an EDGAR client *and* a vector store blurs it.

## Consequences

- `sec_edgar` (discovery) remains a graph tool the research agent runs; it hands discovered URLs to `ingest_document`. The `PREREQUISITES` edge renames `rag_search ← sec_edgar` → `ingest_document ← sec_edgar` ([ADR-0010](./0010-env-driven-mcp-transport-and-consumption.md)) — same mechanism, new name.
- Backing-store column names remain filing-flavored internally; the tool contract maps `entity`/`document_type`/`published_at` onto them at the boundary, so the public surface is generic without a store migration.
