# Running the watchlist

The watchlist tracks companies and refreshes them **only when a free Signal
fires** (ADR-0011). This is how to run and verify it.

## Environment

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY`, `TAVILY_API_KEY` | the agent itself (already set on the service) |
| `PGVECTOR_URL` | set → Postgres store; unset → local SQLite under `.agent_runs/` |
| `WATCH_WEBHOOK_URL` | Discord/Slack/ntfy webhook. **Unset = notifications are a silent no-op** |
| `PUBLIC_BASE_URL` | e.g. `https://…up.railway.app`, so notifications can link to the run |
| `OWNER_ID` | defaults to `me`; the single hardcoded owner |

## Manage the watchlist

```bash
curl -X POST "$BASE/watchlist" -H 'Content-Type: application/json' \
     -d '{"name":"Apple Inc.","cik":320193,"ticker":"AAPL"}'
curl "$BASE/watchlist"                      # each company + latest run + delta
curl -X DELETE "$BASE/watchlist/cik:320193"
```

**Supply the CIK when you can.** It is the preferred identity: it survives
renames and ticker changes, and it is what the filing Signal polls. A company
added with a name only still works, but it can only be refreshed manually or on
a price move.

A price Signal means the price moved more than 7% *since the last Refresh*, so
a move the agent has already researched never fires twice.

## Sweep

```bash
python watch_worker.py --dry-run          # poll Signals, report, spend NOTHING
python watch_worker.py                    # sweep for real
python watch_worker.py --company cik:320193
```

**Always dry-run first after changing the watchlist.** It tells you exactly
which companies would be refreshed — i.e. what the sweep is about to cost —
without spending anything.

A company with no Run yet is a **baseline**: its first sweep refreshes
unconditionally, because there is nothing to compare against. After that it is
Signal-gated.

## Railway cron service

The worker deliberately does **not** live in the web service — a deploy or
restart would kill a Refresh mid-flight, and a 5–7 minute LLM job has no
business competing with the request path.

As deployed (service `watch-worker`, production):

| Setting | Value | Why |
|---|---|---|
| Source | `railway up --service watch-worker` from the repo | same Dockerfile as the web service |
| Start command | `python watch_worker.py` | overrides the Dockerfile's `server.py` |
| Cron schedule | `30 22 * * 1-5` (UTC) | weekdays 6:30pm ET: after the close, so the day's close is in, and after most after-hours earnings 8-Ks |
| Restart policy | `NEVER` | a crash must not loop into repeated paid sweeps; the missing `/sweeps` row is the alarm |
| Variables | references to `financial-research-agent`: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `CLAUDE_MODEL`, `PGVECTOR_URL`, `PGVECTOR_COLLECTION`; plus `PUBLIC_BASE_URL` | Railway variables are per-service; references mean no secret is copied and a rotated key reaches the worker too |

Add `WATCH_WEBHOOK_URL` to the worker when you have a webhook; until then it
sweeps and records, but notifies nobody.

**Deploying code changes:** the worker is not redeployed with the web service.
After changing anything the worker imports, run
`railway up --service watch-worker --detach` as well.

**Create the service before giving it source.** An empty service cannot run, so
set the start command, schedule and restart policy first, then deploy — never
the other way round, or an unscheduled service boots and sweeps immediately.

A cron service runs on schedule and exits, so you pay execution time rather
than an always-on second service.

## Verify it is alive

```bash
curl "$BASE/sweeps"
```

Every sweep is recorded **even when it changed nothing**. That is the whole
point: without it, "no alerts this week" is ambiguous between *nothing happened*
and *the cron is dead*, and a tool relied on for money decisions cannot carry
that ambiguity. If the newest sweep is older than your schedule, the worker —
not the market — is the thing that went quiet.

## What it will not catch

Signals are filings and price moves. A lawsuit, a CEO departure or a
short-seller report fires **nothing** until it reaches a filing or moves the
price. That blind spot is deliberate (ADR-0011); manual Refresh covers it, and a
news signal is deferred rather than free.
