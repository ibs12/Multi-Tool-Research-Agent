"""
api/main.py
-----------
FastAPI application exposing the Financial Research Agent over HTTP.

Endpoints:
  POST /research          — run agent, return full report as JSON (batch)
  POST /research/stream   — run agent, stream node updates as SSE events
  GET  /health            — liveness check
  GET  /docs              — auto-generated Swagger UI (FastAPI built-in)

Server-Sent Events (SSE) format:
  Each node completion is sent as:
    data: {"event": "node_complete", "node": "supervisor", "data": {...}}

  Final report is sent as:
    data: {"event": "report", "data": "<markdown string>"}

  Errors are sent as:
    data: {"event": "error", "data": "<message>"}

  Stream ends with:
    data: {"event": "done"}

Interview talking point:
  SSE is preferred over WebSockets here because the communication is
  strictly one-directional (server pushes, client only reads). SSE is
  simpler, works over plain HTTP/1.1, and is natively supported by
  EventSource in all modern browsers — no socket management needed.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

load_dotenv()

from agent.graph import graph
from agent.state import make_initial_state

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Financial Research Agent",
    description="Multi-tool AI research agent powered by Claude + LangGraph",
    version="1.0.0",
)

# Allow all origins in dev — restrict in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Serve frontend ────────────────────────────────────────────────────────────
import os as _os
_frontend_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "frontend")
if _os.path.exists(_frontend_dir):
    app.mount("/app", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

@app.get("/", include_in_schema=False)
def root():
    """Redirect root to the frontend."""
    index = _os.path.join(_frontend_dir, "index.html")
    if _os.path.exists(index):
        return FileResponse(index)
    return {"message": "Financial Research Agent API", "docs": "/docs", "frontend": "/app"}


# ── Request / Response models ─────────────────────────────────────────────────

class ResearchRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=5,
        max_length=500,
        example="Analyse JPMorgan Chase investment outlook",
    )
    max_iterations: int = Field(default=8, ge=1, le=16)


class ResearchResponse(BaseModel):
    query: str
    company_target: str
    final_report: str
    tools_called: list[str]
    iteration_count: int
    elapsed_seconds: float
    error: str | None


# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "service": "financial-research-agent"}


# ── Batch endpoint ────────────────────────────────────────────────────────────

@app.post("/research", response_model=ResearchResponse, tags=["Research"])
def research(req: ResearchRequest):
    """
    Run the full research agent and return the complete report as JSON.
    Blocks until the agent finishes — use /research/stream for live updates.
    """
    start = time.time()
    state = make_initial_state(req.query, max_iterations=req.max_iterations)

    try:
        result = graph.invoke(state)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return ResearchResponse(
        query=req.query,
        company_target=result.get("company_target", ""),
        final_report=result.get("final_report", ""),
        tools_called=result.get("tools_called", []),
        iteration_count=result.get("iteration_count", 0),
        elapsed_seconds=round(time.time() - start, 2),
        error=result.get("error"),
    )


# ── Streaming SSE endpoint ────────────────────────────────────────────────────

@app.post("/research/stream", tags=["Research"])
async def research_stream(req: ResearchRequest):
    """
    Stream agent progress as Server-Sent Events.

    Each SSE message is a JSON object with an 'event' field:
      node_complete  — a graph node finished, includes node name + key outputs
      report         — the final synthesised analyst brief (markdown)
      error          — something went wrong
      done           — stream is finished

    Connect with EventSource in the browser or curl:
      curl -N -X POST http://localhost:8000/research/stream \\
           -H "Content-Type: application/json" \\
           -d '{"query": "Analyse Apple Inc."}'
    """
    return StreamingResponse(
        _stream_generator(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


async def _stream_generator(req: ResearchRequest) -> AsyncGenerator[str, None]:
    """
    Streams the graph with stream_mode="values" — each event is the full
    accumulated state after a node completes.  Consecutive snapshots are
    diffed to identify which node just ran and what changed.

    The final snapshot always contains final_report after synthesis, so no
    second graph.invoke() call is needed.
    """
    loop = asyncio.get_event_loop()
    initial_state = make_initial_state(req.query, req.max_iterations)

    def _run():
        return list(graph.stream(initial_state, stream_mode="values"))

    try:
        snapshots = await loop.run_in_executor(None, _run)

        prev = initial_state
        final_state = initial_state

        for state in snapshots:
            final_state = state
            node_name, payload = _diff_state(prev, state)
            if node_name:
                yield _sse({"event": "node_complete", "node": node_name, "data": payload})
                await asyncio.sleep(0)
            prev = state

        report = final_state.get("final_report", "")
        if report:
            yield _sse({"event": "report", "data": report})
        else:
            yield _sse({"event": "error", "data": "No report generated — check server logs."})

        yield _sse({"event": "done"})

    except Exception as e:
        yield _sse({"event": "error", "data": str(e)})
        yield _sse({"event": "done"})


def _diff_state(prev: dict, curr: dict) -> tuple[str | None, dict]:
    """
    Identify which node just ran by diffing two consecutive full state snapshots.

    Detection order (first match wins):
      1. final_report became non-empty  → synthesis
      2. iteration_count increased      → supervisor
      3. tool_results grew              → tool node (or dispatcher if many at once)
    """
    # Synthesis: final_report was written
    curr_report = curr.get("final_report", "")
    if curr_report and not prev.get("final_report", ""):
        return "synthesis", {"complete": True}

    # Supervisor: increments iteration_count each run
    curr_iters = curr.get("iteration_count", 0)
    if curr_iters > prev.get("iteration_count", 0):
        return "supervisor", {
            "plan": curr.get("current_plan", "")[:200],
            "tools_queued": curr.get("tools_remaining", []),
            "iteration": curr_iters,
            "company_target": curr.get("company_target", ""),
        }

    # Tool node(s): tool_results list grew
    prev_results: list = prev.get("tool_results") or []
    curr_results: list = curr.get("tool_results") or []
    new_results = curr_results[len(prev_results):]
    if new_results:
        # Multiple new results in one step → dispatcher ran tools in parallel
        if len(new_results) > 1:
            return "dispatcher", {
                "tools": [r["tool_name"] for r in new_results],
                "tool_results": [
                    {
                        "tool": r["tool_name"],
                        "success": r["success"],
                        "preview": r["output"][:200] if r["success"] else r.get("error", ""),
                    }
                    for r in new_results
                ],
            }
        r = new_results[0]
        return r["tool_name"], {
            "tool": r["tool_name"],
            "success": r["success"],
            "preview": r["output"][:300] if r["success"] else r.get("error", ""),
        }

    return None, {}


def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"