"""
agent/brief_parser.py
─────────────────────
Reads the figures back out of a finished brief.

Synthesis is required to emit a Financial Snapshot table with fixed rows
(Revenue / Gross Margin % / Net Income / EPS) and period columns, so the
structured numbers can be recovered deterministically rather than by asking a
model to re-read its own prose (ADR-era E2.Q3: score what ships, don't build a
shadow path).

Two consumers, one parser:
  - the eval wants ONE period (the case's period) → `extract_period`
  - the watchlist wants EVERY period, so deltas can compare like with like
    across runs — including forward estimates moving (ADR-0012)
    → `extract_all_periods`

Best-effort by construction: a cell it cannot read yields no entry, and a
missing entry must be treated as *unknown*, never as zero and never as a change.
"""

from __future__ import annotations

import re

_ROW_TO_FIELD = {
    "revenue": "revenue", "net income": "net_income",
    "eps": "eps", "gross margin": "gross_margin",
}
_NUM = re.compile(r"\$?\s*(-?[\d,]+(?:\.\d+)?)\s*(billion|million|b|m|%)?", re.I)
_EMPTY_CELL = {"", "—", "-", "–", "n/a", "N/A"}
_YEARISH = re.compile(r"(FY)?\s*20\d{2}", re.I)


def _to_number(text: str, field: str = ""):
    """First number in a cell, normalised to absolute units ($391.0B → 3.91e11)."""
    m = _NUM.search(text)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    if unit in ("billion", "b"):
        val *= 1e9
    elif unit in ("million", "m"):
        val *= 1e6
    return val


def _cells(line: str) -> list[str]:
    """Split a markdown table row, dropping the empty cells the outer pipes
    produce so the label lands at index 0 and columns align with the header."""
    parts = [c.strip() for c in line.split("|")]
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(c) <= set("-: ") for c in cells if c)


def _table_rows(brief: str) -> list[list[str]]:
    return [_cells(ln) for ln in brief.splitlines() if ln.count("|") >= 2]


def _find_header(rows: list[list[str]]) -> list[str] | None:
    """The header is the first row carrying at least two year-like columns."""
    for cells in rows:
        if _is_separator(cells):
            continue
        if sum(1 for c in cells if _YEARISH.search(c)) >= 2:
            return cells
    return None


def extract_all_periods(brief: str) -> dict[str, dict[str, float]]:
    """Every period column in the snapshot table.

    Returns {period_label: {field: value}}, e.g.
        {"FY2024": {"revenue": 3.91e11, "gross_margin": 46.2}, "FY2026E": {...}}
    Period labels are kept verbatim from the header ("FY2024", "Q3 FY2026",
    "FY2026E") so successive runs compare like with like.
    """
    rows = _table_rows(brief)
    header = _find_header(rows)
    if not header:
        return {}

    periods: dict[str, dict[str, float]] = {}
    for cells in rows:
        if cells == header or _is_separator(cells) or not cells:
            continue
        label = cells[0].lower()
        field = next((f for key, f in _ROW_TO_FIELD.items() if key in label), None)
        if not field:
            continue
        for i in range(1, min(len(cells), len(header))):
            cell = cells[i]
            if cell in _EMPTY_CELL:
                continue
            period = header[i]
            if not _YEARISH.search(period):
                continue
            value = _to_number(cell, field)
            if value is not None:
                periods.setdefault(period, {})[field] = value
    return periods


def extract_period(brief: str, period: str) -> dict:
    """One period's figures, shaped for the eval: {field: {value, cited_accession}}.

    `cited_accession` stays None — the brief cites source *types* ([SEC Filing]),
    not accession numbers; verifying a figure against the retrieved chunks is a
    separate step (E2.Q4).
    """
    want = period.replace("FY", "").strip()
    all_periods = extract_all_periods(brief)
    for label, fields in all_periods.items():
        if want and want in label:
            return {f: {"value": v, "cited_accession": None} for f, v in fields.items()}
    return {}
