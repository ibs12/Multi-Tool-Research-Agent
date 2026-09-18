"""
agent/mcp_client.py
───────────────────
The one MCP client the agents consume the RAG server through (ADR-0010).

- **One client, constructed once** (module-level singleton). Tools are static,
  so per-request construction is pure overhead; each tool call opens its own
  transport session internally.
- **Env-driven transport** — `stdio` (server run as a subprocess) locally and
  in CI/eval; `streamable-http` (a URL) in deploy — mirroring how PGVECTOR_URL
  selects the store backend. Agents always go through this client, so local/CI
  runs exercise the real MCP protocol path.
- **Per-agent binding** — `tools_for(role)` returns the ADR-0007 roster as
  wiring: research binds ingest + search; risk/compliance bind search only.
- **Wrapped for the migration** — everything MCP-adapter-specific lives here,
  so the future `langchain.mcp` (`MCPAdapter`) swap is a one-file change.

Construction is lazy so importing this module (and the graph) never requires
the MCP packages to be installed until an agent actually reaches for a tool.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "document-rag"

# Per-agent tool binding (ADR-0010 = the ADR-0007 roster restated as wiring).
ROLE_TOOLS: dict[str, list[str]] = {
    "research":   ["ingest_document", "search_documents"],
    "risk":       ["search_documents"],
    "compliance": ["search_documents"],
}


def _connection() -> dict:
    """The MultiServerMCPClient connection for this environment (ADR-0010).

    `stdio` (default): launch `python -m mcp_server.server` as a subprocess with
    the repo root as cwd and the current env propagated (so PGVECTOR_URL — or
    its absence, → embedded Chroma — carries through, giving the zero-external-
    deps CI path). `streamable-http`: point at a running server URL.
    """
    t = os.getenv("MCP_TRANSPORT", "stdio").lower()
    if t in ("http", "streamable-http", "streamable_http"):
        return {
            "transport": "streamable_http",
            "url": os.getenv("MCP_SERVER_URL", "http://localhost:8100/mcp"),
        }
    return {
        "transport": "stdio",
        "command": os.getenv("MCP_SERVER_PYTHON", sys.executable),
        "args": ["-m", "mcp_server.server"],
        "cwd": str(_REPO_ROOT),
        "env": {**os.environ},
    }


_client = None
_tools_cache: dict | None = None


def get_client():
    """The one `MultiServerMCPClient`, constructed once. Isolated here so the
    future `langchain.mcp` migration touches only this function."""
    global _client
    if _client is None:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        _client = MultiServerMCPClient({SERVER_NAME: _connection()})
    return _client


async def _all_tools() -> dict:
    """name → BaseTool, fetched once (tools are static)."""
    global _tools_cache
    if _tools_cache is None:
        tools = await get_client().get_tools(server_name=SERVER_NAME)
        _tools_cache = {t.name: t for t in tools}
    return _tools_cache


async def tools_for(role: str) -> list:
    """The MCP tools an agent `role` ('research' | 'risk' | 'compliance') binds."""
    allowed = ROLE_TOOLS.get(role, [])
    tools = await _all_tools()
    return [tools[n] for n in allowed if n in tools]


async def get_tool(name: str):
    """Fetch a single MCP tool by name — for programmatic ingest/search from a
    node (vs. binding the whole set to an LLM agent)."""
    return (await _all_tools()).get(name)


def reset() -> None:
    """Drop the cached client/tools. For tests that swap MCP_TRANSPORT/env."""
    global _client, _tools_cache
    _client = None
    _tools_cache = None
