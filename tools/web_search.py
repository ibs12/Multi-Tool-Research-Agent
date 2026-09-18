"""
tools/web_search.py
───────────────────
Tavily-powered web search tool, purpose-built for LLM agents.
Includes result scoring and financial domain filtering.

Uses AsyncTavilyClient for non-blocking I/O when available (tavily-python
>=0.3.0).  Falls back to asyncio.to_thread wrapping the sync client so the
event loop is never blocked regardless of the installed version.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

try:
    from tavily import AsyncTavilyClient
    _HAS_ASYNC = True
except ImportError:
    _HAS_ASYNC = False
    try:
        from tavily import TavilyClient
    except ImportError:
        TavilyClient = None  # type: ignore

# ── Trusted financial news / data domains ──────────────────────────────────────
FINANCIAL_DOMAINS = [
    "reuters.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "cnbc.com",
    "sec.gov",
    "finance.yahoo.com",
    "marketwatch.com",
    "seekingalpha.com",
    "investopedia.com",
    "morningstar.com",
]

MIN_RELEVANCE_SCORE = 0.4

# Error sentinel — a call "failed" iff its output starts with this. Imported by
# web_search_node so the tool and its node can't drift apart (ADR-0003).
WEBSEARCH_ERROR = "[WebSearch Error]"


def _build_kwargs(query: str, max_results: int, financial_only: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "query":               query,
        "max_results":         max_results,
        "search_depth":        "advanced",
        "include_answer":      True,
        "include_raw_content": False,
    }
    if financial_only:
        kwargs["include_domains"] = FINANCIAL_DOMAINS
    return kwargs


async def run_web_search(query: str, financial_only: bool = True) -> str:
    """
    Async entry point called by web_search_node.
    Uses AsyncTavilyClient when available; falls back to sync client in a thread.
    Never raises — returns an error string on failure.
    """
    try:
        kwargs = _build_kwargs(query, max_results=5, financial_only=financial_only)
        if _HAS_ASYNC:
            client = AsyncTavilyClient(api_key=os.environ["TAVILY_API_KEY"])
            response = await client.search(**kwargs)
        else:
            if TavilyClient is None:
                raise ImportError("Run: pip install tavily-python")
            def _sync():
                return TavilyClient(api_key=os.environ["TAVILY_API_KEY"]).search(**kwargs)
            response = await asyncio.to_thread(_sync)
        return _format_results(query, response)
    except ImportError as e:
        return f"{WEBSEARCH_ERROR} Missing dependency: {e}"
    except KeyError:
        return f"{WEBSEARCH_ERROR} TAVILY_API_KEY not set in environment."
    except Exception as e:
        return f"{WEBSEARCH_ERROR} {type(e).__name__}: {e}"


def _format_results(query: str, response: dict[str, Any]) -> str:
    lines = [f"WEB SEARCH RESULTS for: '{query}'", "=" * 60]

    if answer := response.get("answer"):
        lines += ["[Tavily Summary]", answer, ""]

    results = response.get("results", [])
    if not results:
        lines.append("No results found.")
        return "\n".join(lines)

    filtered = [r for r in results if r.get("score", 0) >= MIN_RELEVANCE_SCORE]
    if not filtered:
        filtered = results[:3]

    for i, r in enumerate(filtered, 1):
        lines += [
            f"[{i}] {r.get('title', 'No title')}",
            f"    Source: {r.get('url', '')}",
            f"    Date:   {r.get('published_date', 'date unknown')}  |  Relevance: {r.get('score', 0):.2f}",
            f"    {r.get('content', '')[:400]}",
            "",
        ]

    return "\n".join(lines)
