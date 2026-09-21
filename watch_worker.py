"""
watch_worker.py
───────────────
The refresh worker — one sweep of the watchlist. This is the cron entry point
(ADR-0011): poll free Signals, spend a Refresh only where one fired, notify only
where something material changed.

Deliberately NOT in the web service: a deploy or restart would kill a Refresh
mid-flight, and a 5-7 minute LLM job has no business competing with the request
path.

    python watch_worker.py --dry-run    # poll signals, spend nothing
    python watch_worker.py              # sweep for real
    python watch_worker.py --company cik:320193

Every sweep is recorded even when it changes nothing, so "no alerts this week"
can be told apart from "the cron is dead".
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()

from agent.deltas import compute_delta, summarise_delta
from agent.notify import notify
from agent.signals import detect, newest_accession
from api.run_store import (build_run_record, list_watchlist, record_sweep,
                           runs_for_company, save_run, set_last_seen)


def _permalink(run_id: str | None) -> str | None:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    return f"{base}/?run={run_id}" if base and run_id else None


async def _refresh(company: dict, signal: dict, previous: dict | None) -> tuple[dict, str | None]:
    """Run the agent for one company and persist the result."""
    from agent.graph import graph
    from agent.nodes.synthesis import synthesis_node
    from agent.state import make_initial_state

    query = f"Analyse {company['name']} investment outlook"
    started = time.time()
    final = await graph.ainvoke(make_initial_state(query, agent_mode="multi"))

    if final.get("termination_reason") == "escalated":
        report = ""                       # escalation ships no brief (ADR-0009)
    else:
        report = (await asyncio.to_thread(synthesis_node, final)).get("final_report", "")

    record = build_run_record(query, "multi", final, report,
                              round(time.time() - started, 2), signal=signal)
    record["company_key"] = company["company_key"]
    run_id = await save_run(record)
    record["id"] = run_id
    return record, run_id


async def sweep(dry_run: bool = False, only: str | None = None) -> dict:
    companies = await list_watchlist()
    if only:
        companies = [c for c in companies if c["company_key"] == only]

    started_at = datetime.now(timezone.utc).isoformat()
    checked = refreshed = notified = 0
    error = None

    for company in companies:
        checked += 1
        key, name = company["company_key"], company["name"]
        history = await runs_for_company(key, limit=1)

        # A company with no Run yet has nothing to compare against: its first
        # Refresh is a baseline, not news.
        signal = ({"kind": "baseline", "detail": "first run for this company"}
                  if not history else detect(company))

        # Record the newest accession AFTER deciding, and regardless of whether
        # we refresh — it is the baseline the next sweep compares against, and
        # letting it drift would make a Signal fire forever.
        if company.get("cik"):
            newest = newest_accession(company["cik"])
            if newest and newest != company.get("last_seen_accession"):
                await set_last_seen(key, newest)

        if not signal:
            print(f"  · {name}: no signal", flush=True)
            continue
        if dry_run:
            print(f"  ⇒ {name}: WOULD refresh — {signal.get('detail')}", flush=True)
            continue

        print(f"  ⇒ {name}: refreshing — {signal.get('detail')}", flush=True)
        try:
            record, run_id = await _refresh(company, signal, history[0] if history else None)
        except Exception as exc:                     # one company must not sink the sweep
            error = f"{name}: {exc}"
            print(f"    ! failed: {exc}", flush=True)
            continue
        refreshed += 1

        delta = compute_delta(history[0] if history else None, record)
        escalated = record.get("termination_reason") == "escalated"
        if not delta["is_empty"] or escalated:
            text = (f"{name}: escalated — {(record.get('escalation') or {}).get('reason', '')}"
                    if escalated else summarise_delta(delta, name))
            if notify(text, _permalink(run_id)):
                notified += 1
        else:
            print("    (no material change — not notifying)", flush=True)

    sweep_id = await record_sweep(checked, refreshed, notified,
                                  started_at=started_at, error=error)
    summary = {"sweep_id": sweep_id, "checked": checked, "refreshed": refreshed,
               "notified": notified, "dry_run": dry_run, "error": error}
    print(f"sweep: checked={checked} refreshed={refreshed} notified={notified}"
          + (" (dry run)" if dry_run else ""), flush=True)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Sweep the watchlist for changes.")
    ap.add_argument("--dry-run", action="store_true",
                    help="poll signals and report what would refresh; spends nothing")
    ap.add_argument("--company", default=None, help="limit to one company_key")
    args = ap.parse_args()
    asyncio.run(sweep(dry_run=args.dry_run, only=args.company))


if __name__ == "__main__":
    main()
