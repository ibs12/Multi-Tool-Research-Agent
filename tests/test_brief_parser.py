"""
tests/test_brief_parser.py
--------------------------
Reading figures back out of a brief — the numbers watchlist deltas compare
(ADR-0012) and the eval scores. Found by the 2026-09-27 extraction eval: losses
were read as profits (Boeing FY2023 −$2.22B stored as +$2.22B), and "H1: $45.2B"
cells — a format synthesis is told to use — were read as 1.
"""

from __future__ import annotations

import pytest

from agent.brief_parser import _to_number, extract_all_periods
from agent.deltas import compute_delta


@pytest.mark.parametrize("cell, expected", [
    # losses, every way a brief writes them
    ("-$2.22B", -2.22e9), ("–$2.22B", -2.22e9), ("−$2.2B", -2.2e9), ("$-2.22B", -2.22e9),
    ("($2.22B)", -2.22e9), ("$(2.22)B", -2.22e9), ("(12.5%)", -12.5), ("-2.22B ¹", -2.22e9),
    # ordinary figures are untouched
    ("$391.0B ¹", 3.91e11), ("$99.80B", 9.98e10), ("43.3% ¹", 43.3), ("$6.13", 6.13),
    ("$1,234M", 1.234e9), ("$2.1 trillion", 2.1e12),
    # an estimate range is not a negative: the dash is a separator
    ("$8.82 (Low $8.28 – High $8.94) ³", 8.82),
    ("$477.8B (Low $471.8B – High $484.0B)", 4.778e11),
    # a period label is not the figure
    ("H1: $45.2B", 4.52e10), ("Q3: $29.8B ²", 2.98e10),
])
def test_cell_values(cell, expected):
    assert _to_number(cell) == pytest.approx(expected)


BRIEF = """## Financial Snapshot
| Metric | FY2022 | FY2023 |
|---|---|---|
| Revenue | $66.6B ¹ | $77.8B ¹ |
| Net Income | ($5.05B) ¹ | -$2.22B ¹ |
"""


def test_a_loss_is_stored_as_a_loss():
    figs = extract_all_periods(BRIEF)
    assert figs["FY2022"]["net_income"] == pytest.approx(-5.05e9)
    assert figs["FY2023"]["net_income"] == pytest.approx(-2.22e9)


def test_a_swing_from_loss_to_profit_is_a_material_change():
    """With the sign dropped, −$5B → +$5B read as no change at all."""
    before = {"figures": extract_all_periods(BRIEF.replace("-$2.22B", "-$5.00B"))}
    after = {"figures": extract_all_periods(BRIEF.replace("-$2.22B", "$5.00B"))}
    changes = compute_delta(before, after)["figure_changes"]
    assert [(c["period"], c["field"]) for c in changes] == [("FY2023", "net_income")]
    assert changes[0]["before"] < 0 < changes[0]["after"]
