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
  report_chunk    — one synthesis token    {"event","data": "<text>"}
  report          — full final report      {"event","data": "<markdown>"}
  error           — something went wrong   {"event","data": "<message>"}
  done            — stream closed          {"event"}

Synthesis streaming architecture:
  stream_synthesis() is a sync generator (runs in a ThreadPoolExecutor).
  An asyncio.Queue bridges the generator thread to the async SSE generator
  so tokens arrive at the browser in real time rather than all at once.

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


class ResearchResponse(BaseModel):
    query: str
    company_target: str
    final_report: str
    tools_called: list[str]
    iteration_count: int
    elapsed_seconds: float
    error: str | None


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "service": "financial-research-agent"}


# ── Batch endpoint ────────────────────────────────────────────────────────────

@app.post("/research", response_model=ResearchResponse, tags=["Research"])
def research(req: ResearchRequest):
    """Run the full agent and return the complete report as JSON."""
    start = time.time()
    state = make_initial_state(req.query, max_iterations=req.max_iterations)

    try:
        result    = graph.invoke(state)
        synthesis = synthesis_node(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ResearchResponse(
        query=req.query,
        company_target=result.get("company_target", ""),
        final_report=synthesis.get("final_report", ""),
        tools_called=result.get("tools_called", []),
        iteration_count=result.get("iteration_count", 0),
        elapsed_seconds=round(time.time() - start, 2),
        error=synthesis.get("error"),
    )


# ── Streaming SSE endpoint ────────────────────────────────────────────────────

@app.post("/research/stream", tags=["Research"])
async def research_stream(req: ResearchRequest):
    """
    Stream agent progress as Server-Sent Events.

    Phase 1 — graph loop: emits node_complete events for each supervisor
               and dispatcher hop (full state diffed to identify the node).
    Phase 2 — synthesis:  emits report_chunk for every token Claude streams,
               then report with the complete markdown when done.
    """
    return StreamingResponse(
        _stream_generator(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_generator(req: ResearchRequest) -> AsyncGenerator[str, None]:
    loop = asyncio.get_event_loop()
    initial_state = make_initial_state(req.query, req.max_iterations)

    # ── Phase 1: stream the research loop ────────────────────────────────────
    def _run_graph():
        return list(graph.stream(initial_state, stream_mode="values"))

    try:
        snapshots = await loop.run_in_executor(None, _run_graph)
    except Exception as e:
        yield _sse({"event": "error", "data": str(e)})
        yield _sse({"event": "done"})
        return

    prev       = initial_state
    final_state = initial_state

    for state in snapshots:
        final_state = state
        node_name, payload = _diff_state(prev, state)
        if node_name:
            yield _sse({"event": "node_complete", "node": node_name, "data": payload})
            await asyncio.sleep(0)
        prev = state

    # ── Phase 2: stream synthesis token-by-token ─────────────────────────────
    # stream_synthesis() is a blocking generator — bridge to async via Queue.
    yield _sse({"event": "node_complete", "node": "synthesis", "data": {"streaming": True}})
    await asyncio.sleep(0)

    q: thread_queue.Queue = thread_queue.Queue()

    def _producer():
        try:
            for event_type, data in stream_synthesis(final_state):
                q.put((event_type, data))
        except Exception as exc:
            q.put(("error", str(exc)))
        finally:
            q.put(None)  # sentinel

    threading.Thread(target=_producer, daemon=True).start()

    full_report = ""
    while True:
        item = await loop.run_in_executor(None, q.get)
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

    return None, {}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"
