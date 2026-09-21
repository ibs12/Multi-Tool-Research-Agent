"""
api/main.py
-----------
FastAPI application exposing the Financial Research Agent over HTTP.

Endpoints:
  POST /research          — run agent, return full report as JSON (batch)
  POST /research/stream   — run agent, stream node updates + synthesis tokens as SSE
  GET  /health            — liveness check
  GET  /docs              — Swagger UI

SSE event types:
  node_complete   — a graph node finished  {"event","node","data"}
                    (node ∈ tools, supervisor, dispatcher, risk_analyst,
                     compliance_checker, synthesis)
  forecast        — structured outlook     {"event","data": {...}}
  escalation      — compliance escalated   {"event","data": {package, verdict}}
                    (terminal — no report follows; ADR-0009)
  report_chunk    — one synthesis token    {"event","data": "<text>"}
  report          — full final report      {"event","data": "<markdown>"}
  saved           — run persisted          {"event","data": {"run_id": "<id>"}}
                    (permalink: GET /runs/{run_id})
  error           — something went wrong   {"event","data": "<message>"}
  done            — stream closed          {"event"}

Concurrency model:
  _semaphore(3) caps concurrent streaming requests.
  graph.astream() is awaited natively — no threads or queues needed for the
  graph phase.  Supervisor (sync) runs in LangGraph's thread executor;
  async tool nodes (web_search, sec_edgar, rag_search) are awaited directly.
  Synthesis uses a thread + thread_queue.Queue bridge because stream_synthesis()
  is a sync generator wrapping the Anthropic streaming SDK.

Interview talking point:
  SSE is preferred over WebSockets here because communication is strictly
  server→client.  SSE works over plain HTTP/1.1, is natively supported by
  EventSource in all modern browsers, and needs no socket management.
"""

from __future__ import annotations

import asyncio
import json
import queue as thread_queue
import threading
import time
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv()

from agent.graph import graph
from agent.nodes.synthesis import synthesis_node, stream_synthesis
from agent.state import make_initial_state
from agent.brief_parser import extract_all_periods
from agent.deltas import compute_delta
from api.run_store import (add_to_watchlist, build_run_record, company_key_for,
                           get_run, list_watchlist, recent_sweeps,
                           remove_from_watchlist, runs_for_company, save_run)

import os as _os
app = FastAPI(
    title="Financial Research Agent",
    description="Multi-tool AI research agent powered by Claude + LangGraph",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_frontend_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "frontend")
if _os.path.exists(_frontend_dir):
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

# Limit concurrent streaming requests to avoid OOM on Railway's 512 MB plan.
_semaphore = asyncio.Semaphore(3)


@app.get("/", include_in_schema=False)
def root():
    index = _os.path.join(_frontend_dir, "index.html")
    if _os.path.exists(index):
        return FileResponse(index)
    return {"message": "Financial Research Agent API", "docs": "/docs"}


# ── Models ────────────────────────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=5, max_length=500,
                       example="Analyse JPMorgan Chase investment outlook")
    max_iterations: int = Field(default=8, ge=1, le=16)
    # "multi" (research → risk → compliance) or "single" (the pre-multi-agent
    # baseline). None → the AGENT_MODE env default. Per-request override lets the
    # E5 before/after run both arms against one server.
    agent_mode: str | None = Field(default=None, pattern="^(multi|single)$")


class ResearchResponse(BaseModel):
    query: str
    company_target: str
    final_report: str
    tools_called: list[str]
    iteration_count: int
    elapsed_seconds: float
    error: str | None
    forecast: dict | None = None
    termination_reason: str | None = None
    # Populated instead of final_report when compliance escalated (ADR-0009).
    escalation: dict | None = None
    # Permalink id — GET /runs/{run_id} replays this run. None if saving failed.
    run_id: str | None = None


def _resolve_agent_mode(req: "ResearchRequest") -> str:
    return req.agent_mode or _os.getenv("AGENT_MODE", "multi")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "service": "financial-research-agent"}


# ── Batch endpoint ────────────────────────────────────────────────────────────

# The persisted run shape is shared with the refresh worker, so a cron-produced
# run is indistinguishable from one triggered by hand.
def _run_record(query: str, agent_mode: str, result: dict,
                final_report: str, elapsed: float) -> dict:
    return build_run_record(query, agent_mode, result, final_report, elapsed)


@app.post("/research", response_model=ResearchResponse, tags=["Research"])
async def research(req: ResearchRequest):
    """Run the full agent and return the complete report as JSON."""
    start = time.time()
    agent_mode = _resolve_agent_mode(req)
    state = make_initial_state(req.query, max_iterations=req.max_iterations,
                               agent_mode=agent_mode)

    try:
        # The dispatcher node is async-only, so the graph must be driven through
        # the async API; langgraph's sync runner rejects it.
        result = await graph.ainvoke(state)
        # Escalation is terminal — no brief (ADR-0009).
        if result.get("termination_reason") == "escalated":
            synthesis = {"final_report": "", "error": None}
        else:
            # Blocking Anthropic call — keep it off the event loop.
            synthesis = await asyncio.to_thread(synthesis_node, result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    elapsed = round(time.time() - start, 2)
    run_id = await save_run(_run_record(
        req.query, agent_mode, result, synthesis.get("final_report", ""), elapsed))

    return ResearchResponse(
        query=req.query,
        company_target=result.get("company_target", ""),
        final_report=synthesis.get("final_report", ""),
        tools_called=result.get("tools_called", []),
        iteration_count=result.get("iteration_count", 0),
        elapsed_seconds=elapsed,
        error=synthesis.get("error"),
        forecast=result.get("forecast"),
        termination_reason=result.get("termination_reason"),
        escalation=(result.get("handoff", {}) or {}).get("escalation"),
        run_id=run_id,
    )


# ── Watchlist ─────────────────────────────────────────────────────────────────
# Companies tracked over time. A watchlist is not a portfolio: no share counts,
# no cost basis (CONTEXT.md). Single hardcoded owner, no auth.

class WatchRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Company name")
    cik: int | None = Field(default=None, description="SEC CIK — preferred identity")
    ticker: str | None = Field(default=None, max_length=10)


@app.get("/watchlist", tags=["Watchlist"])
async def read_watchlist():
    """The tracked companies, each with its latest run and what changed."""
    companies = await list_watchlist()
    out = []
    for company in companies:
        runs = await runs_for_company(company["company_key"], limit=2)
        latest = runs[0] if runs else None
        delta = compute_delta(runs[1], runs[0]) if len(runs) >= 2 else None
        out.append({
            **company,
            "latest_run": ({"id": latest.get("id"),
                            "created_at": latest.get("created_at"),
                            "termination_reason": latest.get("termination_reason"),
                            "verdict": (latest.get("compliance_verdict") or {}).get("verdict"),
                            "figures": latest.get("figures") or {}} if latest else None),
            "delta": delta,
        })
    return out


@app.post("/watchlist", tags=["Watchlist"])
async def create_watch(req: WatchRequest):
    entry = await add_to_watchlist(req.name, req.cik, req.ticker)
    if not entry:
        raise HTTPException(status_code=503, detail="Watchlist storage unavailable")
    return entry


@app.delete("/watchlist/{company_key}", tags=["Watchlist"])
async def delete_watch(company_key: str):
    if not await remove_from_watchlist(company_key):
        raise HTTPException(status_code=503, detail="Watchlist storage unavailable")
    return {"removed": company_key}


@app.get("/companies/{company_key}", tags=["Watchlist"])
async def read_company(company_key: str, limit: int = 10):
    """A company's run timeline plus the delta between its two most recent runs."""
    runs = await runs_for_company(company_key, limit=limit)
    if not runs:
        raise HTTPException(status_code=404, detail="No runs for this company")
    return {
        "company_key": company_key,
        "company_target": runs[0].get("company_target"),
        "delta": compute_delta(runs[1], runs[0]) if len(runs) >= 2 else None,
        "runs": [{"id": r.get("id"), "created_at": r.get("created_at"),
                  "agent_mode": r.get("agent_mode"),
                  "termination_reason": r.get("termination_reason"),
                  "verdict": (r.get("compliance_verdict") or {}).get("verdict"),
                  "figures": r.get("figures") or {}} for r in runs],
    }


@app.get("/sweeps", tags=["Watchlist"])
async def read_sweeps(limit: int = 5):
    """Recent watchlist sweeps — the evidence the refresh worker is alive.

    A sweep is recorded even when it changed nothing, so an empty change feed
    can be told apart from a dead cron (ADR-0011).
    """
    return await recent_sweeps(limit=limit)


@app.get("/runs/{run_id}", tags=["Research"])
async def read_run(run_id: str):
    """Replay a saved run — the permalink behind a shared report.

    Anyone with the (unguessable) id can read it; there is no auth.
    """
    record = await get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail="Run not found")
    return record


# ── Streaming SSE endpoint ────────────────────────────────────────────────────

@app.post("/research/stream", tags=["Research"])
async def research_stream(req: ResearchRequest):
    """
    Stream agent progress as Server-Sent Events.

    Phase 1 — graph loop: emits node_complete events as each node finishes,
               delivered in real time via graph.astream().
    Phase 2 — synthesis:  emits report_chunk per token, then the full report.

    Returns 503 immediately when all 3 concurrent slots are occupied.
    """
    if _semaphore.locked():
        raise HTTPException(
            status_code=503,
            detail="Agent at capacity — please try again shortly.",
        )
    return StreamingResponse(
        _stream_generator(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_generator(req: ResearchRequest) -> AsyncGenerator[str, None]:
    async with _semaphore:
        started = time.time()
        agent_mode = _resolve_agent_mode(req)
        initial_state = make_initial_state(req.query, req.max_iterations,
                                           agent_mode=agent_mode)

        # ── Phase 1: native async graph streaming ─────────────────────────────
        # graph.astream() awaits async nodes directly and runs sync nodes
        # (supervisor) in LangGraph's internal thread executor — no manual
        # thread management or queue bridging needed.
        prev        = initial_state
        final_state = initial_state

        try:
            async for state in graph.astream(initial_state, stream_mode="values"):
                final_state = state
                node_name, payload = _diff_state(prev, state)
                if node_name:
                    yield _sse({"event": "node_complete", "node": node_name, "data": payload})
                    await asyncio.sleep(0)
                prev = state
        except Exception as e:
            yield _sse({"event": "error", "data": str(e)})
            yield _sse({"event": "done"})
            return

        # ── Escalation is terminal — emit the package, skip synthesis (ADR-0009) ─
        if final_state.get("termination_reason") == "escalated":
            handoff = final_state.get("handoff", {}) or {}
            yield _sse({
                "event": "escalation",
                "data": {
                    "package": handoff.get("escalation", {}),
                    "verdict": handoff.get("compliance_verdict", {}),
                },
            })
            run_id = await save_run(_run_record(
                req.query, agent_mode, final_state, "", round(time.time() - started, 2)))
            if run_id:
                yield _sse({"event": "saved", "data": {"run_id": run_id}})
            yield _sse({"event": "done"})
            return

        # ── Phase 2: synthesis streaming (sync generator → thread bridge) ─────
        # stream_synthesis() wraps the Anthropic sync streaming SDK, so it
        # must run in a thread.  thread_queue.Queue + asyncio.to_thread bridges
        # the blocking q.get() back to the async generator.
        # Structured forecast: sent before synthesis so the chart data is in the
        # browser by the time the report renders.
        forecast = final_state.get("forecast")
        if forecast:
            yield _sse({"event": "forecast", "data": forecast})
            await asyncio.sleep(0)

        term = final_state.get("termination_reason")
        if term and term != "completed":
            yield _sse({"event": "meta", "data": {"termination_reason": term}})
            await asyncio.sleep(0)

        yield _sse({"event": "node_complete", "node": "synthesis", "data": {"streaming": True}})
        await asyncio.sleep(0)

        synth_q: thread_queue.Queue = thread_queue.Queue()

        def _producer():
            try:
                for event_type, data in stream_synthesis(final_state):
                    synth_q.put((event_type, data))
            except Exception as exc:
                synth_q.put(("error", str(exc)))
            finally:
                synth_q.put(None)

        threading.Thread(target=_producer, daemon=True).start()

        full_report = ""
        while True:
            item = await asyncio.to_thread(synth_q.get)
            if item is None:
                break
            event_type, data = item
            if event_type == "chunk":
                yield _sse({"event": "report_chunk", "data": data})
                await asyncio.sleep(0)
            elif event_type == "done":
                full_report = data
            elif event_type == "error":
                yield _sse({"event": "error", "data": data})

        if full_report:
            yield _sse({"event": "report", "data": full_report})
            run_id = await save_run(_run_record(
                req.query, agent_mode, final_state, full_report,
                round(time.time() - started, 2)))
            if run_id:
                yield _sse({"event": "saved", "data": {"run_id": run_id}})
        else:
            yield _sse({"event": "error", "data": "No report generated — check server logs."})

        yield _sse({"event": "done"})


# ── State diffing ─────────────────────────────────────────────────────────────

def _diff_state(prev: dict, curr: dict) -> tuple[str | None, dict]:
    """
    Identify which node just ran by diffing two consecutive full state snapshots.

    Detection order (first match wins):
      1. iteration_count increased  → supervisor
      2. tool_results grew          → dispatcher (or individual tool if 1 new result)
    """
    curr_iters = curr.get("iteration_count", 0)
    if curr_iters > prev.get("iteration_count", 0):
        return "supervisor", {
            "plan":           curr.get("current_plan", "")[:200],
            "tools_queued":   curr.get("tools_remaining", []),
            "iteration":      curr_iters,
            "company_target": curr.get("company_target", ""),
        }

    prev_results: list = prev.get("tool_results") or []
    curr_results: list = curr.get("tool_results") or []
    new_results = curr_results[len(prev_results):]
    if new_results:
        if len(new_results) > 1:
            return "dispatcher", {
                "tools": [r["tool_name"] for r in new_results],
                "tool_results": [
                    {
                        "tool":    r["tool_name"],
                        "success": r["success"],
                        "preview": r["output"][:200] if r["success"] else r.get("error", ""),
                    }
                    for r in new_results
                ],
            }
        r = new_results[0]
        return r["tool_name"], {
            "tool":    r["tool_name"],
            "success": r["success"],
            "preview": r["output"][:300] if r["success"] else r.get("error", ""),
        }

    # 3. handoff contract grew → a specialized agent ran (risk / compliance).
    prev_ho: dict = prev.get("handoff") or {}
    curr_ho: dict = curr.get("handoff") or {}
    if "risk_assessment" in curr_ho and "risk_assessment" not in prev_ho:
        ra = curr_ho["risk_assessment"] or {}
        return "risk_analyst", {
            "risk_summary": ra.get("risk_summary", ""),
            "red_flags":    ra.get("red_flags", []),
        }
    if "compliance_verdict" in curr_ho and "compliance_verdict" not in prev_ho:
        cv = curr_ho["compliance_verdict"] or {}
        return "compliance_checker", {
            "verdict":  cv.get("verdict", ""),
            "reasons":  cv.get("reasons", []),
            "gap_type": cv.get("gap_type"),
        }

    return None, {}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
