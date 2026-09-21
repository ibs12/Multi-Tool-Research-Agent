"""
api/run_store.py
────────────────
Persistence for the watchlist and for completed runs.

Backend is env-selected, mirroring the vector store (ADR-0005):

    PGVECTOR_URL set   → PostgreSQL via asyncpg (the deployed path)
    PGVECTOR_URL unset → SQLite file under .agent_runs/ (local dev, CI)

Both expose the same async API, so nothing upstream branches on the backend.

Two shapes live here:
  - **Run** — one completed agent run (a brief or an escalation), addressable by
    a permalink id, carrying the structured figures deltas are computed from.
  - **Watchlist** — the companies being tracked. A watchlist is not a portfolio:
    no share counts, no cost basis (see CONTEXT.md).

Every row carries `owner_id`. There is no auth and one hardcoded owner — it is a
single column so that becoming multi-user later is "add authentication" rather
than "reshape the data", and it is deliberately the ONLY concession made to that
possibility.

Saving must never break a run: `save_run` returns None on any failure and the
caller carries on without a permalink.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_RUNS = "agent_runs"
_WATCH = "watchlist"
_SWEEPS = "watch_sweeps"
_SQLITE_PATH = Path(__file__).resolve().parent.parent / ".agent_runs" / "runs.db"

DEFAULT_OWNER = os.getenv("OWNER_ID", "me")

_pool = None
_pool_lock = asyncio.Lock()
_schema_ready = False


def _pg_url() -> str | None:
    return os.getenv("PGVECTOR_URL") or None


def new_run_id() -> str:
    """Short, unguessable, URL-safe."""
    return secrets.token_urlsafe(9)


def company_key_for(name: str | None, cik: int | str | None = None) -> str:
    """A stable identity for a tracked company, so successive Runs line up.

    CIK is preferred — it survives renames and ticker changes. Otherwise fall
    back to a slug of the name with any trailing "(TICKER)" stripped, so
    "Apple Inc. (AAPL)" and "Apple Inc." are the same company.
    """
    if cik not in (None, "", 0):
        try:
            return f"cik:{int(cik)}"
        except (TypeError, ValueError):
            pass
    base = re.sub(r"\s*\([A-Z.]{1,6}\)\s*$", "", (name or "").strip())
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return f"name:{slug}" if slug else "name:unknown"


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
            CREATE TABLE IF NOT EXISTS {_RUNS} (
                id                 TEXT PRIMARY KEY,
                created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
                query              TEXT,
                agent_mode         TEXT,
                termination_reason TEXT,
                payload            JSONB NOT NULL
            )""")
        # The runs table predates the watchlist: add the new columns in place.
        await conn.execute(f"ALTER TABLE {_RUNS} ADD COLUMN IF NOT EXISTS owner_id TEXT")
        await conn.execute(f"ALTER TABLE {_RUNS} ADD COLUMN IF NOT EXISTS company_key TEXT")
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS {_RUNS}_company_idx "
            f"ON {_RUNS} (owner_id, company_key, created_at DESC)")
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_WATCH} (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                company_key TEXT NOT NULL,
                name        TEXT NOT NULL,
                cik         BIGINT,
                ticker      TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (owner_id, company_key)
            )""")
        await conn.execute(
            f"ALTER TABLE {_WATCH} ADD COLUMN IF NOT EXISTS last_seen_accession TEXT")
        # A sweep is recorded even when it changes nothing, so silence is
        # distinguishable from a dead cron (ADR-0011).
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_SWEEPS} (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                started_at  TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                checked     INTEGER NOT NULL DEFAULT 0,
                refreshed   INTEGER NOT NULL DEFAULT 0,
                notified    INTEGER NOT NULL DEFAULT 0,
                error       TEXT
            )""")


async def _pg(query: str, *args, fetch: str = "none"):
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if fetch == "row":
            return await conn.fetchrow(query, *args)
        if fetch == "all":
            return await conn.fetch(query, *args)
        return await conn.execute(query, *args)


# ── SQLite (local / CI) ───────────────────────────────────────────────────────

def _sqlite_conn() -> sqlite3.Connection:
    _SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _sqlite_add_column(conn, table: str, name: str, decl: str) -> None:
    """SQLite has no ADD COLUMN IF NOT EXISTS — check the pragma."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def _sqlite_ensure_schema_sync() -> None:
    with _sqlite_conn() as conn:
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_RUNS} (
                id                 TEXT PRIMARY KEY,
                created_at         TEXT NOT NULL,
                query              TEXT,
                agent_mode         TEXT,
                termination_reason TEXT,
                payload            TEXT NOT NULL
            )""")
        _sqlite_add_column(conn, _RUNS, "owner_id", "TEXT")
        _sqlite_add_column(conn, _RUNS, "company_key", "TEXT")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {_RUNS}_company_idx "
                     f"ON {_RUNS} (owner_id, company_key, created_at DESC)")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_WATCH} (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                company_key TEXT NOT NULL,
                name        TEXT NOT NULL,
                cik         INTEGER,
                ticker      TEXT,
                created_at  TEXT NOT NULL,
                UNIQUE (owner_id, company_key)
            )""")
        _sqlite_add_column(conn, _WATCH, "last_seen_accession", "TEXT")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_SWEEPS} (
                id          TEXT PRIMARY KEY,
                owner_id    TEXT NOT NULL,
                started_at  TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                checked     INTEGER NOT NULL DEFAULT 0,
                refreshed   INTEGER NOT NULL DEFAULT 0,
                notified    INTEGER NOT NULL DEFAULT 0,
                error       TEXT
            )""")


def _sqlite_save_sync(run_id: str, record: dict) -> None:
    with _sqlite_conn() as conn:
        conn.execute(
            f"""INSERT OR IGNORE INTO {_RUNS}
                (id, created_at, query, agent_mode, termination_reason,
                 owner_id, company_key, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, record.get("created_at"), record.get("query"),
             record.get("agent_mode"), record.get("termination_reason"),
             record.get("owner_id"), record.get("company_key"),
             json.dumps(record)))


def _sqlite_get_sync(run_id: str) -> dict | None:
    with _sqlite_conn() as conn:
        row = conn.execute(f"SELECT payload FROM {_RUNS} WHERE id = ?", (run_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def _sqlite_runs_for_company_sync(owner: str, key: str, limit: int) -> list[dict]:
    with _sqlite_conn() as conn:
        rows = conn.execute(
            f"""SELECT payload FROM {_RUNS}
                WHERE owner_id = ? AND company_key = ?
                ORDER BY created_at DESC LIMIT ?""", (owner, key, limit)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def _sqlite_watch_write_sync(op: str, **kw):
    with _sqlite_conn() as conn:
        if op == "add":
            conn.execute(
                f"""INSERT OR IGNORE INTO {_WATCH}
                    (id, owner_id, company_key, name, cik, ticker, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (kw["id"], kw["owner"], kw["key"], kw["name"], kw["cik"],
                 kw["ticker"], kw["created_at"]))
        elif op == "remove":
            conn.execute(f"DELETE FROM {_WATCH} WHERE owner_id = ? AND company_key = ?",
                         (kw["owner"], kw["key"]))


def _sqlite_watch_list_sync(owner: str) -> list[dict]:
    with _sqlite_conn() as conn:
        rows = conn.execute(
            f"""SELECT id, company_key, name, cik, ticker, created_at, last_seen_accession
                FROM {_WATCH} WHERE owner_id = ? ORDER BY name""", (owner,)).fetchall()
    return [dict(r) for r in rows]


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
        record = {
            **record,
            "id": run_id,
            "created_at": record.get("created_at") or datetime.now(timezone.utc).isoformat(),
            "owner_id": record.get("owner_id") or DEFAULT_OWNER,
            "company_key": record.get("company_key")
                           or company_key_for(record.get("company_target") or record.get("query")),
        }
        if _pg_url():
            await _pg(
                f"""INSERT INTO {_RUNS} (id, query, agent_mode, termination_reason,
                                          owner_id, company_key, payload)
                    VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb) ON CONFLICT (id) DO NOTHING""",
                run_id, record.get("query"), record.get("agent_mode"),
                record.get("termination_reason"), record["owner_id"],
                record["company_key"], json.dumps(record))
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
            row = await _pg(f"SELECT payload, created_at FROM {_RUNS} WHERE id = $1",
                            run_id, fetch="row")
            if not row:
                return None
            rec = json.loads(row["payload"]) if isinstance(row["payload"], str) \
                else dict(row["payload"])
            rec.setdefault("created_at", row["created_at"].isoformat())
            return rec
        return await asyncio.to_thread(_sqlite_get_sync, run_id)
    except Exception:
        return None


async def runs_for_company(company_key: str, limit: int = 10,
                           owner: str | None = None) -> list[dict]:
    """A company's Runs, newest first — the timeline a delta is computed from."""
    owner = owner or DEFAULT_OWNER
    try:
        await _ensure_schema()
        if _pg_url():
            rows = await _pg(
                f"""SELECT payload FROM {_RUNS}
                    WHERE owner_id = $1 AND company_key = $2
                    ORDER BY created_at DESC LIMIT $3""",
                owner, company_key, limit, fetch="all")
            return [json.loads(r["payload"]) if isinstance(r["payload"], str)
                    else dict(r["payload"]) for r in rows]
        return await asyncio.to_thread(_sqlite_runs_for_company_sync,
                                       owner, company_key, limit)
    except Exception:
        return []


async def add_to_watchlist(name: str, cik: int | None = None, ticker: str | None = None,
                           owner: str | None = None) -> dict | None:
    """Track a company. Idempotent on (owner, company_key)."""
    owner = owner or DEFAULT_OWNER
    key = company_key_for(name, cik)
    entry = {"id": new_run_id(), "owner": owner, "key": key, "name": name,
             "cik": int(cik) if cik not in (None, "", 0) else None,
             "ticker": (ticker or None),
             "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        await _ensure_schema()
        if _pg_url():
            await _pg(
                f"""INSERT INTO {_WATCH} (id, owner_id, company_key, name, cik, ticker)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    ON CONFLICT (owner_id, company_key) DO NOTHING""",
                entry["id"], owner, key, name, entry["cik"], entry["ticker"])
        else:
            await asyncio.to_thread(_sqlite_watch_write_sync, "add", **entry)
        return {"id": entry["id"], "company_key": key, "name": name,
                "cik": entry["cik"], "ticker": entry["ticker"]}
    except Exception:
        return None


async def remove_from_watchlist(company_key: str, owner: str | None = None) -> bool:
    owner = owner or DEFAULT_OWNER
    try:
        await _ensure_schema()
        if _pg_url():
            await _pg(f"DELETE FROM {_WATCH} WHERE owner_id = $1 AND company_key = $2",
                      owner, company_key)
        else:
            await asyncio.to_thread(_sqlite_watch_write_sync, "remove",
                                    owner=owner, key=company_key)
        return True
    except Exception:
        return False


async def list_watchlist(owner: str | None = None) -> list[dict]:
    owner = owner or DEFAULT_OWNER
    try:
        await _ensure_schema()
        if _pg_url():
            rows = await _pg(
                f"""SELECT id, company_key, name, cik, ticker, created_at, last_seen_accession
                    FROM {_WATCH} WHERE owner_id = $1 ORDER BY name""",
                owner, fetch="all")
            return [dict(r) for r in rows]
        return await asyncio.to_thread(_sqlite_watch_list_sync, owner)
    except Exception:
        return []


def _sqlite_set_last_seen_sync(owner: str, key: str, accession: str | None) -> None:
    with _sqlite_conn() as conn:
        conn.execute(f"UPDATE {_WATCH} SET last_seen_accession = ? "
                     f"WHERE owner_id = ? AND company_key = ?", (accession, owner, key))


def _sqlite_record_sweep_sync(row: dict) -> None:
    with _sqlite_conn() as conn:
        conn.execute(
            f"""INSERT INTO {_SWEEPS}
                (id, owner_id, started_at, finished_at, checked, refreshed, notified, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["id"], row["owner_id"], row["started_at"], row["finished_at"],
             row["checked"], row["refreshed"], row["notified"], row["error"]))


def _sqlite_recent_sweeps_sync(owner: str, limit: int) -> list[dict]:
    with _sqlite_conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM {_SWEEPS} WHERE owner_id = ?
                ORDER BY started_at DESC LIMIT ?""", (owner, limit)).fetchall()
    return [dict(r) for r in rows]


async def set_last_seen(company_key: str, accession: str | None,
                        owner: str | None = None) -> bool:
    """Record the newest accession observed for a company.

    Updated on every sweep, refresh or not — it is the baseline the next sweep
    compares against, so letting it drift would make a Signal fire forever.
    """
    owner = owner or DEFAULT_OWNER
    try:
        await _ensure_schema()
        if _pg_url():
            await _pg(f"UPDATE {_WATCH} SET last_seen_accession = $1 "
                      f"WHERE owner_id = $2 AND company_key = $3",
                      accession, owner, company_key)
        else:
            await asyncio.to_thread(_sqlite_set_last_seen_sync, owner, company_key, accession)
        return True
    except Exception:
        return False


async def record_sweep(checked: int, refreshed: int, notified: int,
                       started_at: str | None = None, error: str | None = None,
                       owner: str | None = None) -> str | None:
    """Record that a sweep ran — including a sweep that changed nothing.

    Without this, "no alerts this week" is ambiguous between "nothing happened"
    and "the cron is dead", and a tool relied on for money decisions cannot
    carry that ambiguity (ADR-0011).
    """
    owner = owner or DEFAULT_OWNER
    now = datetime.now(timezone.utc).isoformat()
    row = {"id": new_run_id(), "owner_id": owner, "started_at": started_at or now,
           "finished_at": now, "checked": checked, "refreshed": refreshed,
           "notified": notified, "error": error}
    try:
        await _ensure_schema()
        if _pg_url():
            await _pg(f"""INSERT INTO {_SWEEPS}
                          (id, owner_id, started_at, finished_at, checked,
                           refreshed, notified, error)
                          VALUES ($1,$2,$3::timestamptz,$4::timestamptz,$5,$6,$7,$8)""",
                      row["id"], owner, row["started_at"], row["finished_at"],
                      checked, refreshed, notified, error)
        else:
            await asyncio.to_thread(_sqlite_record_sweep_sync, row)
        return row["id"]
    except Exception:
        return None


async def recent_sweeps(limit: int = 5, owner: str | None = None) -> list[dict]:
    """Recent sweeps, newest first — the evidence that the worker is alive."""
    owner = owner or DEFAULT_OWNER
    try:
        await _ensure_schema()
        if _pg_url():
            rows = await _pg(f"""SELECT * FROM {_SWEEPS} WHERE owner_id = $1
                                 ORDER BY started_at DESC LIMIT $2""",
                             owner, limit, fetch="all")
            return [dict(r) for r in rows]
        return await asyncio.to_thread(_sqlite_recent_sweeps_sync, owner, limit)
    except Exception:
        return []


def build_run_record(query: str, agent_mode: str, result: dict,
                     final_report: str, elapsed: float,
                     signal: dict | None = None) -> dict:
    """The persisted shape of a finished run (brief or escalation).

    Shared by the API and the refresh worker so a cron-produced run is
    indistinguishable from one you triggered by hand. `figures` is the
    structured read of the snapshot table — the object deltas compare (ADR-0012).
    """
    from agent.brief_parser import extract_all_periods
    handoff = result.get("handoff", {}) or {}
    company = result.get("company_target", "")
    return {
        "query": query,
        "company_target": company,
        "company_key": company_key_for(company or query),
        "figures": extract_all_periods(final_report) if final_report else {},
        "agent_mode": agent_mode,
        "termination_reason": result.get("termination_reason"),
        "final_report": final_report,
        "escalation": handoff.get("escalation"),
        "compliance_verdict": handoff.get("compliance_verdict"),
        "risk_assessment": handoff.get("risk_assessment"),
        "forecast": result.get("forecast"),
        "tools_called": result.get("tools_called", []),
        "iteration_count": result.get("iteration_count", 0),
        "elapsed_seconds": elapsed,
        "model": os.getenv("CLAUDE_MODEL", "claude-opus-4-8"),
        "signal": signal,
    }
