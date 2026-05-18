"""
rag/sec_fetcher.py
------------------
Downloads SEC filing documents and splits them into semantic chunks.

Design decisions:
  - Fetches HTML directly from SEC EDGAR (no third-party needed)
  - Strips HTML tags to get clean text
  - Splits on SEC section headers (Item 1, Item 7, etc.)
  - Keeps chunks at ~500 tokens to balance context and precision
  - Caches fetched documents to avoid re-downloading on repeated queries

Interview talking point:
  "I chunk on SEC section boundaries rather than fixed character counts.
   Item 7 is MD&A, Item 8 is Financial Statements, Item 1A is Risk Factors.
   Chunking this way means a query for 'revenue growth' lands in MD&A,
   not in the middle of a footnote."
"""

from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache

EDGAR_HEADERS = {
    "User-Agent": "FinancialResearchAgent/1.0 research@example.com",
    "Accept": "text/html,application/xhtml+xml",
}

# SEC 10-K/10-Q section headers we want to split on
SEC_SECTIONS = [
    r"item\s+1[^0-9a]",      # Item 1 - Business
    r"item\s+1a",             # Item 1A - Risk Factors
    r"item\s+1b",             # Item 1B - Unresolved Staff Comments
    r"item\s+2[^0-9]",       # Item 2 - Properties
    r"item\s+3[^0-9]",       # Item 3 - Legal Proceedings
    r"item\s+7[^a^0-9]",     # Item 7 - MD&A
    r"item\s+7a",             # Item 7A - Quantitative Disclosures
    r"item\s+8[^0-9]",       # Item 8 - Financial Statements
    r"item\s+9[^a^0-9]",     # Item 9 - Changes in Accountants
]

SECTION_PATTERN = re.compile(
    "|".join(SEC_SECTIONS),
    re.IGNORECASE,
)

MAX_CHUNK_CHARS = 2000   # ~500 tokens
MIN_CHUNK_CHARS = 100    # discard tiny fragments
REQUEST_DELAY   = 0.5    # seconds between SEC requests (rate limit)


@dataclass
class FilingChunk:
    """One semantically meaningful chunk from a SEC filing."""
    text:        str
    source_url:  str
    form_type:   str
    company:     str
    filed_at:    str
    section:     str        # e.g. "Item 7 - MD&A"
    chunk_index: int


def fetch_and_chunk(
    url:       str,
    form_type: str,
    company:   str,
    filed_at:  str,
) -> list[FilingChunk]:
    """
    Download a SEC filing document and split into chunks.

    Returns an empty list on any fetch/parse error — the caller
    should handle this gracefully and continue without RAG data.
    """
    try:
        raw_html = _fetch_html(url)
        if not raw_html:
            return []

        clean_text = _strip_html(raw_html)
        chunks     = _split_into_chunks(clean_text)

        return [
            FilingChunk(
                text=chunk_text,
                source_url=url,
                form_type=form_type,
                company=company,
                filed_at=filed_at,
                section=_label_section(chunk_text),
                chunk_index=i,
            )
            for i, chunk_text in enumerate(chunks)
        ]
    except Exception as e:
        return []


# -- Internal helpers ---------------------------------------------------------

@lru_cache(maxsize=32)
def _fetch_html(url: str) -> str:
    """Fetch HTML from SEC with caching and rate limiting."""
    time.sleep(REQUEST_DELAY)
    try:
        req = urllib.request.Request(url, headers=EDGAR_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            content = r.read()
            # Try UTF-8 first, fall back to latin-1
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                return content.decode("latin-1")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    """Remove HTML tags and normalise whitespace."""
    # Remove script and style blocks entirely
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#160;", " ")
    # Collapse whitespace
    text = re.sub(r"\s{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _split_into_chunks(text: str) -> list[str]:
    """
    Split on SEC section boundaries first, then by character limit.
    Preserves semantic coherence better than fixed-size chunking.
    """
    # Split on section headers
    sections = SECTION_PATTERN.split(text)

    chunks = []
    for section in sections:
        section = section.strip()
        if len(section) < MIN_CHUNK_CHARS:
            continue

        # If section is small enough, keep as one chunk
        if len(section) <= MAX_CHUNK_CHARS:
            chunks.append(section)
            continue

        # Otherwise split by paragraph, accumulating up to MAX_CHUNK_CHARS
        paragraphs = section.split("\n\n")
        current = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) > MAX_CHUNK_CHARS and current:
                chunks.append(current.strip())
                current = para
            else:
                current = current + "\n\n" + para if current else para
        if current and len(current) >= MIN_CHUNK_CHARS:
            chunks.append(current.strip())

    return chunks


def _label_section(text: str) -> str:
    """Identify which SEC section a chunk belongs to."""
    lower = text[:200].lower()
    labels = {
        "item 7a": "Item 7A - Quantitative Market Risk",
        "item 7":  "Item 7 - MD&A",
        "item 8":  "Item 8 - Financial Statements",
        "item 1a": "Item 1A - Risk Factors",
        "item 1":  "Item 1 - Business",
        "item 2":  "Item 2 - Properties",
        "item 3":  "Item 3 - Legal Proceedings",
    }
    for key, label in labels.items():
        if key in lower:
            return label
    return "General"