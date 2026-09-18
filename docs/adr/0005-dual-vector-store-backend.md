# Dual vector-store backend, selected at import by PGVECTOR_URL

The RAG layer supports two interchangeable vector stores behind one uniform async interface (`rag/rag_backend.py`): **pgvector** on PostgreSQL when `PGVECTOR_URL` is set (production, Docker Compose, and the Railway deploy), and **ChromaDB** as a keyless local fallback when it is unset. We keep both rather than collapsing to one.

## Why both

- **Zero-Docker local run.** A reviewer can `pip install -r requirements.txt && python run.py "Analyse Apple"` and see the agent work with no Postgres and no `PGVECTOR_URL` — ChromaDB persists to a local `.chroma_db/`. Lowering friction on the "let me try it" moment is worth real weight for a job-search artifact ([ADR-0001](./0001-optimize-for-legibility-over-production-hardening.md)).
- **Production story intact.** pgvector keeps the ACID vector store with a SQL `WHERE company = ?` filter that prevents cross-company result contamination — and it is what the Railway deploy runs. Collapsing to ChromaDB would lose that and break the deploy.

## Why it stays cheap

Policy and error logic are **backend-agnostic** — the ingest orchestration, the ingest-skip decision, and the `[RAG Error]` sentinel all live in `rag/rag_search.py` (`run_rag_pipeline` / `run_rag_query`), not in the stores. The per-backend files are thin CRUD (`ingest_chunks` / `query` / `collection_stats`), and both are idempotent by a `md5(source_url:chunk_index)` chunk id. A correctness fix to RAG lands **once**, in the pipeline layer — not per backend.

## Considered options

- **pgvector only.** Loses the zero-Docker local run — the reviewer needs Docker + Postgres before anything runs. Rejected.
- **ChromaDB only.** Loses the ACID / SQL-filter production story and breaks the Railway deploy. Rejected.

## Consequence

Only the pgvector path runs in the demo and deploy, so the **ChromaDB path is under-exercised and can rot silently**. It needs a smoke test that runs the ingest → query round-trip against the ChromaDB backend so a regression there is caught, not discovered by the one reviewer who runs it without Docker.
