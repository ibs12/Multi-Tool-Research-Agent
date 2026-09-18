# Tools report failure via a tagged error string, not exceptions or typed results

Each tool's `run_*` function catches its own exceptions and returns a plain string; on failure the string is prefixed with a tag like `[SEC EDGAR Error]`. The tool node maps that to `ToolResult.success`/`.error` by checking the prefix (`success = not output.startswith("[X Error]")`). We keep this stringly-typed convention rather than moving to exceptions or a typed `ToolOutput` — it reads clearly and keeps every tool a simple `str -> str` function — but we harden it against the two ways it silently fails.

## Considered options

- **Typed result / raise-and-catch.** Tools return `ToolOutput(text, ok, error)` or raise a typed `ToolError` caught by one wrapper; success is *reported*, never *parsed*. More honest, kills the drift class outright — but a seven-`run_*`-signature refactor for a convention whose prefixes currently all match. Deferred as premature under [ADR-0001](./0001-optimize-for-legibility-over-production-hardening.md). Revisit if the tool contract grows richer (e.g. tools needing to return structured payloads alongside text).
- **Keep the tagged string, hardened (chosen).**

## The two hardening rules (both are load-bearing)

1. **One prefix, one source of truth.** Each tool's error tag is a single shared constant imported by both the tool and its node — never two hand-typed string literals in two files that can drift apart and turn a real error into a silent `success=True`.
2. **The sentinel must be detectable regardless of output composition.** A tool whose output concatenates parts must not bury the failure tag behind a non-error prefix. This rule exists because `rag_search` returned `f"{ingest_status}\n\n{query_result}"` — so a `[RAG Error]` from the query phase never appeared at `startswith` position, and failed searches were fed to synthesis as if they were filing data.

## Consequence

Nodes (and any multi-step pipeline a node calls, e.g. `run_rag_pipeline`) must not raise: the "never raise; return a tagged error" contract is what lets the supervisor decide whether to proceed on partial data instead of crashing the graph. A tool that lets an exception escape breaks the contract even if its prefixes are correct.
