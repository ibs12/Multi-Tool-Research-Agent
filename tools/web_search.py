"""
tools/web_search.py
───────────────────
Tavily-powered web search tool, purpose-built for LLM agents.
Includes retry logic, result scoring, and financial domain filtering.

Why Tavily over raw Google/Bing?
  - Returns pre-chunked, LLM-friendly results (no HTML parsing needed)
  - Supports domain filtering (e.g. reuters.com, sec.gov, ft.com)
  - Relevance scores let us drop low-quality hits before they reach Claude
"""

from __future__ import annotations

import os
from typing import Any
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Lazy import so the module loads without the package installed
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

# Minimum Tavily relevance score to include a result (0–1)
MIN_RELEVANCE_SCORE = 0.4


class WebSearchTool:
    """
    Wraps the Tavily API with financial-domain filtering and retry logic.

    Usage:
        tool = WebSearchTool()
        result = tool.search("Apple Q4 2024 earnings sentiment")
    """

    def __init__(self, api_key: str | None = None) -> None:
        if TavilyClient is None:
            raise ImportError("Run: pip install tavily-python")
        self._client = TavilyClient(api_key=api_key or os.environ["TAVILY_API_KEY"])

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def search(
        self,
        query: str,
        max_results: int = 5,
        financial_only: bool = True,
    ) -> str:
        """
        Run a web search and return a formatted string ready for Claude.

        Args:
            query:          The search query.
            max_results:    How many results to return (capped at 10 by Tavily).
            financial_only: If True, restricts to FINANCIAL_DOMAINS.

        Returns:
            A formatted multi-line string with title, URL, date, and snippet
            for each result — directly injectable into a Claude prompt.
        """
        kwargs: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced",   # more thorough than "basic"
            "include_answer": True,        # Tavily's own AI summary (useful sanity check)
            "include_raw_content": False,  # keep tokens manageable
        }
        if financial_only:
            kwargs["include_domains"] = FINANCIAL_DOMAINS

        response = self._client.search(**kwargs)

        return _format_results(query, response)


def _format_results(query: str, response: dict[str, Any]) -> str:
    """
    Converts raw Tavily response into a compact, structured string.
    Claude handles plain text better than nested JSON in the tool result slot.
    """
    lines = [f"WEB SEARCH RESULTS for: '{query}'", "=" * 60]

    # Tavily's own synthesised answer (when available)
    if answer := response.get("answer"):
        lines += ["[Tavily Summary]", answer, ""]

    results = response.get("results", [])
    if not results:
        lines.append("No results found.")
        return "\n".join(lines)

    # Filter by relevance score
    filtered = [
        r for r in results
        if r.get("score", 0) >= MIN_RELEVANCE_SCORE
    ]
    if not filtered:
        filtered = results[:3]  # fallback: always show at least 3

    for i, r in enumerate(filtered, 1):
        title = r.get("title", "No title")
        url = r.get("url", "")
        date = r.get("published_date", "date unknown")
        snippet = r.get("content", "")[:400]  # keep context window sane
        score = r.get("score", 0)

        lines += [
            f"[{i}] {title}",
            f"    Source: {url}",
            f"    Date:   {date}  |  Relevance: {score:.2f}",
            f"    {snippet}",
            "",
        ]

    return "\n".join(lines)


# ── Convenience function (used by the LangGraph tool node) ────────────────────

def run_web_search(query: str, financial_only: bool = True) -> str:
    """
    Top-level function called directly by web_search_node in agent/nodes/tools.py.
    Returns a formatted string or an error message (never raises).
    """
    try:
        tool = WebSearchTool()
        return tool.search(query, financial_only=financial_only)
    except ImportError as e:
        return f"[WebSearch Error] Missing dependency: {e}"
    except KeyError:
        return "[WebSearch Error] TAVILY_API_KEY not set in environment."
    except Exception as e:
        return f"[WebSearch Error] {type(e).__name__}: {e}"