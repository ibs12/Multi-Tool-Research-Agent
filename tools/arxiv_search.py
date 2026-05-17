"""
tools/arxiv_search.py
─────────────────────
ArXiv paper search tool — surfaces relevant quantitative finance and
AI/ML research to give the analyst brief academic grounding.

Role in the agent:
  When researching topics like credit risk, fraud detection, or algorithmic
  trading, citing a recent arXiv paper signals that the agent is aware of
  the state-of-the-art — a differentiating touch vs a pure news summary.

Design decisions:
  - Restricts to quantitative finance (q-fin) and CS/AI (cs.AI, cs.LG) categories
  - Returns abstracts only (not full papers) to keep token count manageable
  - Sorts by most recent submission date (most relevant for fast-moving fields)
"""

from __future__ import annotations

from datetime import datetime

try:
    import arxiv
    HAS_ARXIV = True
except ImportError:
    HAS_ARXIV = False

# arXiv categories relevant to financial research + AI
FINANCIAL_CATEGORIES = [
    "q-fin",       # quantitative finance (all subcategories)
    "cs.AI",       # artificial intelligence
    "cs.LG",       # machine learning
    "stat.ML",     # statistics — machine learning
]

MAX_RESULTS = 3          # keep context window cost low
MAX_ABSTRACT_CHARS = 600 # truncate abstracts to this length


class ArxivTool:
    """
    Wraps the arxiv Python client with financial/AI category filtering.
    """

    def __init__(self) -> None:
        if not HAS_ARXIV:
            raise ImportError("Run: pip install arxiv")
        self._client = arxiv.Client(
            page_size=MAX_RESULTS,
            delay_seconds=1.0,   # respect arXiv rate limits
            num_retries=3,
        )

    def search(self, query: str, max_results: int = MAX_RESULTS) -> str:
        """
        Search arXiv for papers matching the query within financial/AI categories.

        Builds a compound query: (user query) AND (q-fin OR cs.AI OR cs.LG OR stat.ML)
        This avoids returning physics papers when searching "risk modelling".
        """
        cat_filter = " OR ".join(f"cat:{c}" for c in FINANCIAL_CATEGORIES)
        full_query = f"({query}) AND ({cat_filter})"

        search = arxiv.Search(
            query=full_query,
            max_results=max_results,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )

        results = list(self._client.results(search))

        if not results:
            # Retry without category filter (broadens the net)
            search_broad = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.Relevance,
            )
            results = list(self._client.results(search_broad))

        return _format_results(query, results)


def _format_results(query: str, results: list) -> str:
    """Format arXiv results into clean, citable text."""
    lines = [
        f"ARXIV SEARCH RESULTS for: '{query}'",
        "=" * 60,
    ]

    if not results:
        lines.append("No papers found. Consider broadening the search query.")
        return "\n".join(lines)

    for i, paper in enumerate(results, 1):
        # Truncate abstract at sentence boundary
        abstract = paper.summary.replace("\n", " ")
        if len(abstract) > MAX_ABSTRACT_CHARS:
            cutoff = abstract.rfind('. ', 0, MAX_ABSTRACT_CHARS)
            abstract = abstract[:cutoff + 1] if cutoff > 0 else abstract[:MAX_ABSTRACT_CHARS]
            abstract += " [...]"

        published = paper.published
        date_str = published.strftime("%Y-%m-%d") if isinstance(published, datetime) else str(published)

        authors = ", ".join(a.name for a in paper.authors[:3])
        if len(paper.authors) > 3:
            authors += " et al."

        lines += [
            f"[{i}] {paper.title}",
            f"    Authors:   {authors}",
            f"    Published: {date_str}",
            f"    ArXiv ID:  {paper.entry_id}",
            f"    PDF:       {paper.pdf_url}",
            f"    Abstract:  {abstract}",
            "",
        ]

    return "\n".join(lines)


# ── Convenience function (called by LangGraph tool node) ──────────────────────

def run_arxiv_search(query: str) -> str:
    """
    Top-level entry point for the arxiv LangGraph node.
    Never raises — always returns a string.
    """
    try:
        tool = ArxivTool()
        return tool.search(query)
    except ImportError as e:
        return f"[ArXiv Error] Missing dependency: {e}"
    except Exception as e:
        return f"[ArXiv Error] {type(e).__name__}: {e}"