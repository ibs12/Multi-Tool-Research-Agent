"""
tests/conftest.py
-----------------
Session-wide test config: run the offline suite on the **zero-external-deps
Chroma path** (ADR-0010) — the same fallback the CI eval-gate uses.

The committed `.env` sets `PGVECTOR_URL`, which would make `rag/rag_backend.py`
select the pgvector (Postgres) backend at import time; any store-touching test
would then depend on a live database and fail with an auth/connection error.

We pin `PGVECTOR_URL` empty *here*, before any test module imports
`rag_backend`. Empty (not deleted) so the `load_dotenv(override=False)` calls
scattered across the app can't re-populate it from `.env` — an already-present
key is never overridden. `rag_backend` treats the empty string as unset and
falls back to embedded Chroma. Set `PGVECTOR_URL` in the real environment to
exercise pgvector deliberately; the offline suite stays hermetic.
"""

import os

os.environ["PGVECTOR_URL"] = ""
