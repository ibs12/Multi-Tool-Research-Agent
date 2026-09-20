"""
api/run_store.py
────────────────
Persistence for completed runs, so a brief (or an escalation) has a stable URL
that can be shared and revisited instead of vanishing when the tab closes.

Backend is env-selected, mirroring the vector store (ADR-0005):

    PGVECTOR_URL set   → PostgreSQL via asyncpg (the deployed path)
    PGVECTOR_URL unset → SQLite file under .agent_runs/ (local dev, CI)

Both expose the same async API — `save_run` and `get_run` — so nothing upstream
branches on the backend.

A saved run is readable by anyone holding its link: the id is unguessable
(`secrets.token_urlsafe`), but there is no auth. Don't research anything you
wouldn't publish, and see the note in api/main.py.

Saving must never break a run: `save_run` returns None on any failure and the
caller carries on without a permalink.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_TABLE = "agent_runs"
_SQLITE_PATH = Path(__file__).resolve().parent.parent / ".agent_runs" / "runs.db"

_pool = None
_pool_lock = asyncio.Lock()
_schema_ready = False


def _pg_url() -> str | None:
    return os.getenv("PGVECTOR_URL") or None


def new_run_id() -> str:
    """Short, unguessable, URL-safe."""
    return secrets.token_urlsafe(9)


# ── PostgreSQL ────────────────────────────────────────────────────────────────

async def _get_pool():
    global _pool
    async with _pool_lock:
        if _pool is None or getattr(_pool, "_closed", False):
            import asyncpg
            _pool = await asyncpg.create_pool(_pg_url(), min_size=1, max_size=4)
    return _pool


async def _pg_ensure_schema() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id                 TEXT PRIMARY KEY,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                query              TEXT,
                agent_mode         TEXT,
                termination_reason TEXT,
                payload            JSONB NOT NULL
            )""")


async def _pg_save(run_id: str, record: dict) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"""INSERT INTO {_TABLE} (id, query, agent_mode, termination_reason, payload)
                VALUES ($1, $2, $3, $4, $5::jsonb) ON CONFLICT (id) DO NOTHING""",
            run_id, record.get("query"), record.get("agent_mode"),
            record.get("termination_reason"), json.dumps(record))


async def _pg_get(run_id: str) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT payload, created_at FROM {_TABLE} WHERE id = $1", run_id)
    if not row:
        return None
    rec = json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"])
    rec.setdefault("created_at", row["created_at"].isoformat())
    return rec


# ── SQLite (local / CI) ───────────────────────────────────────────────────────

def _sqlite_conn() -> sqlite3.Connection:
    _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_ensure_schema_sync() -> None:
    with _sqlite_conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id                 TEXT PRIMARY KEY,
                created_at         TEXT NOT NULL,
                query              TEXT,
                agent_mode         TEXT,
                termination_reason TEXT,
                payload            TEXT NOT NULL
            )""")


def _sqlite_save_sync(run_id: str, record: dict) -> None:
    with _sqlite_conn() as conn:
        conn.execute(
            f"""INSERT OR IGNORE INTO {_TABLE}
                (id, created_at, query, agent_mode, termination_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?)""",
            (run_id, record.get("created_at"), record.get("query"),
             record.get("agent_mode"), record.get("termination_reason"),
             json.dumps(record)))


def _sqlite_get_sync(run_id: str) -> dict | None:
    with _sqlite_conn() as conn:
        row = conn.execute(f"SELECT payload FROM {_TABLE} WHERE id = ?", (run_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


# ── Public API ────────────────────────────────────────────────────────────────

async def _ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    if _pg_url():
        await _pg_ensure_schema()
    else:
        await asyncio.to_thread(_sqlite_ensure_schema_sync)
    _schema_ready = True


async def save_run(record: dict) -> str | None:
    """Persist a completed run; returns its id, or None if saving failed.

    Never raises — a storage problem must not fail the research run itself.
    """
    try:
        await _ensure_schema()
        run_id = record.get("id") or new_run_id()
        record = {**record, "id": run_id,
                  "created_at": record.get("created_at") or datetime.now(timezone.utc).isoformat()}
        if _pg_url():
            await _pg_save(run_id, record)
        else:
            await asyncio.to_thread(_sqlite_save_sync, run_id, record)
        return run_id
    except Exception:
        return None


async def get_run(run_id: str) -> dict | None:
    """Fetch a saved run, or None if it doesn't exist / storage is unavailable."""
    try:
        await _ensure_schema()
        if _pg_url():
            return await _pg_get(run_id)
        return await asyncio.to_thread(_sqlite_get_sync, run_id)
    except Exception:
        return None
