"""
scripts/delete_outage_escalations.py
────────────────────────────────────
One-off cleanup: remove the runs the worker saved as "escalations" while the
Anthropic credit was exhausted (Oct 12 – Nov 6 2026). Each is compliance's
fail-safe reacting to "credit balance is too low", not a finding — 34 of them
across all ten companies. Since #63 the worker no longer creates these.

    python scripts/delete_outage_escalations.py            # dry run: list + back up
    python scripts/delete_outage_escalations.py --delete   # back up, then delete

Needs PGVECTOR_URL pointing at the production run store (it runs fine inside
the Railway service, e.g. via `railway ssh`). Safety:
  - matches only escalated runs whose escalation reason names the credit error;
  - always writes a JSON backup of every matched row first;
  - deletes in one transaction, and only if the count still equals --expect.

Side effect worth knowing: removing these makes each company's last *real* run
its price baseline again, so the next sweep may refresh companies whose price
moved >7% since then (~$0.40 each).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

MATCH = """termination_reason = 'escalated'
           AND payload->'escalation'->>'reason' ILIKE '%credit balance%'"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--expect", type=int, default=34, help="refuse unless exactly this many match")
    ap.add_argument("--backup-dir", default=".agent_runs")
    args = ap.parse_args()

    import asyncpg
    url = os.environ["PGVECTOR_URL"]
    conn = await asyncpg.connect(url, timeout=30)
    try:
        rows = await conn.fetch(f"SELECT id, created_at, company_key, payload::text AS payload "
                                f"FROM agent_runs WHERE {MATCH} ORDER BY created_at")
        print(f"matched {len(rows)} outage escalations"
              + (f" ({rows[0]['created_at']:%Y-%m-%d} → {rows[-1]['created_at']:%Y-%m-%d})" if rows else ""))
        by_company: dict = {}
        for r in rows:
            by_company[r["company_key"]] = by_company.get(r["company_key"], 0) + 1
        print("per company:", by_company)

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = Path(args.backup_dir) / f"outage-escalations-backup-{stamp}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps([dict(r, created_at=r["created_at"].isoformat()) for r in rows], indent=1))
        print(f"backup written: {backup}")

        if not args.delete:
            print("dry run — nothing deleted. Re-run with --delete.")
            return
        if len(rows) != args.expect:
            raise SystemExit(f"refusing: {len(rows)} matched, expected {args.expect}")
        async with conn.transaction():
            n = await conn.execute(f"DELETE FROM agent_runs WHERE {MATCH}")
            if n != f"DELETE {args.expect}":
                raise SystemExit(f"refusing: delete reported {n!r}; rolled back")
        print(f"deleted {args.expect} rows.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
