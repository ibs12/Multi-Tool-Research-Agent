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
    Async generator that runs the LangGraph graph in a thread pool
    (graph.stream is synchronous) and yields SSE-formatted strings.
    """
    state = make_initial_state(req.query, max_iterations=req.max_iterations)
    loop = asyncio.get_event_loop()

    def _run_graph():
        """Blocking call — runs in thread pool via run_in_executor."""
        return list(graph.stream(state, stream_mode="updates"))

    try:
        # Run the synchronous graph in a thread so we don't block the event loop
        events = await loop.run_in_executor(None, _run_graph)

        for event in events:
            for node_name, node_output in event.items():
                payload = _summarise_node_output(node_name, node_output)
                yield _sse({"event": "node_complete", "node": node_name, "data": payload})
                await asyncio.sleep(0)   # yield control back to event loop

                # Emit the final report as its own event when synthesis completes
                if node_name == "synthesis" and node_output.get("final_report"):
                    yield _sse({
                        "event": "report",
                        "data": node_output["final_report"],
                    })

        yield _sse({"event": "done"})

    except Exception as e:
        yield _sse({"event": "error", "data": str(e)})
        yield _sse({"event": "done"})


def _summarise_node_output(node_name: str, output: dict) -> dict:
    """
    Extracts the most useful fields from a node's output for the SSE payload.
    Avoids sending raw tool output (can be 2000+ chars) over the wire.
    """
    summary: dict = {"node": node_name}

    if node_name == "supervisor":
        summary["plan"] = output.get("current_plan", "")[:200]
        summary["tools_queued"] = output.get("tools_remaining", [])
        summary["iteration"] = output.get("iteration_count", 0)
        summary["company_target"] = output.get("company_target", "")

    elif node_name in ("web_search", "wikipedia", "calculator", "arxiv"):
        results = output.get("tool_results", [])
        if results:
            last = results[-1]
            summary["tool"] = last["tool_name"]
            summary["success"] = last["success"]
            summary["preview"] = last["output"][:300] if last["success"] else last.get("error", "")

    elif node_name == "synthesis":
        summary["complete"] = True

    return summary


def _sse(data: dict) -> str:
    """Format a dict as an SSE data line."""
    return f"data: {json.dumps(data)}\n\n"