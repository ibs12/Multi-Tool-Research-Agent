"""
tools/wikipedia.py
──────────────────
Wikipedia lookup tool for grounding company/sector background context.

Role in the agent:
  Web search gives us *recent* news; Wikipedia gives us *stable* facts —
  founding date, headquarters, business model, key subsidiaries, sector.
  This reduces hallucination risk when Claude writes the final report.

Design decisions:
  - Fetches the lead section only (avoids dumping 50k tokens into state)
  - Falls back to a search if the exact page title isn't found
  - Strips wikitext markup so the output is clean prose
"""

from __future__ import annotations

import re

try:
    import wikipediaapi
    HAS_WIKI = True
except ImportError:
    HAS_WIKI = False


# ── Max characters to pull from a Wikipedia article ──────────────────────────
# The lead section is usually 500–1500 chars — enough for grounding context.
MAX_CHARS = 2000


class WikipediaTool:
    """
    Thin wrapper around wikipedia-api with financial-agent-friendly defaults.
    """

    def __init__(self) -> None:
        if not HAS_WIKI:
            raise ImportError("Run: pip install wikipedia-api")
        # User-agent required by Wikimedia ToS
        self._wiki = wikipediaapi.Wikipedia(
            language="en",
            user_agent="FinancialResearchAgent/1.0 (Citi Interview Project)",
        )

    def lookup(self, company_name: str) -> str:
        """
        Look up a company or sector by name.
        Tries exact match first, then a cleaned variant (e.g. drops 'Inc.').

        Returns formatted plain text for injection into agent state.
        """
        candidates = _generate_search_candidates(company_name)

        for candidate in candidates:
            page = self._wiki.page(candidate)
            if page.exists():
                return _format_page(company_name, page)

        return (
            f"WIKIPEDIA RESULT for: '{company_name}'\n"
            f"{'=' * 60}\n"
            f"No Wikipedia page found. Tried: {', '.join(candidates)}\n"
            f"Suggest cross-referencing with web search results."
        )


def _generate_search_candidates(name: str) -> list[str]:
    """
    Generate a ranked list of page title candidates to try.

    Examples:
        'Apple Inc.'  → ['Apple Inc.', 'Apple Inc', 'Apple', 'Apple (company)']
        'AAPL'        → ['Apple Inc.', 'AAPL']  (ticker expansion not yet wired)
    """
    # Strip ticker symbol in parentheses e.g. "Citigroup Inc. (C)" -> "Citigroup Inc."
    import re as _re
    name = _re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', name).strip()

    candidates = [name]

    # Strip trailing punctuation variant
    stripped = name.rstrip(".")
    if stripped != name:
        candidates.append(stripped)

    # Drop legal suffixes
    for suffix in [" Inc", " Inc.", " Corp", " Corp.", " Ltd", " LLC", " plc"]:
        cleaned = name.replace(suffix, "").strip()
        if cleaned not in candidates:
            candidates.append(cleaned)

    # Common Wikipedia disambiguation suffix
    candidates.append(f"{candidates[-1]} (company)")

    return candidates


def _format_page(query: str, page: "wikipediaapi.WikipediaPage") -> str:
    """
    Extract and format the lead section of a Wikipedia article.
    """
    # Lead section = text before the first heading
    full_text = page.text
    lead = full_text[:MAX_CHARS]

    # Clean up leftover wikitext artifacts
    lead = re.sub(r'\[\d+\]', '', lead)        # remove citation numbers [1]
    lead = re.sub(r'\s{2,}', ' ', lead)        # collapse whitespace
    lead = lead.strip()

    # Truncate at a sentence boundary if possible
    if len(full_text) > MAX_CHARS:
        last_period = lead.rfind('.')
        if last_period > MAX_CHARS * 0.7:
            lead = lead[:last_period + 1]
        lead += " [truncated]"

    lines = [
        f"WIKIPEDIA RESULT for: '{query}'",
        "=" * 60,
        f"Title:   {page.title}",
        f"URL:     {page.fullurl}",
        "",
        lead,
    ]
    return "\n".join(lines)


# ── Convenience function (called by LangGraph tool node) ──────────────────────

def run_wikipedia(query: str) -> str:
    """
    Top-level entry point for the wikipedia LangGraph node.
    Never raises — always returns a string.
    """
    try:
        tool = WikipediaTool()
        return tool.lookup(query)
    except ImportError as e:
        return f"[Wikipedia Error] Missing dependency: {e}"
    except Exception as e:
        return f"[Wikipedia Error] {type(e).__name__}: {e}"