"""
tests/test_historical_financials.py
-----------------------------------
The historical-financials tool feeds the brief's FY2022–FY2025 columns. The
2026-09-27 extraction eval found it never emitted EPS: 32 of 33 briefs left
every historical EPS cell empty, because synthesis may not invent a figure.
Offline — yfinance is replaced by a stub.
"""

from __future__ import annotations

import pandas as pd
import pytest

from tools import consensus_estimates as ce


class _Ticker:
    def __init__(self, fin):
        self.financials = fin
        self.quarterly_financials = pd.DataFrame()


def _fin(rows: dict) -> pd.DataFrame:
    cols = [pd.Timestamp(f"{y}-09-30") for y in (2022, 2023, 2024, 2025)]
    return pd.DataFrame({c: {k: v[i] for k, v in rows.items()} for i, c in enumerate(cols)})


@pytest.fixture
def stub(monkeypatch):
    def use(rows):
        monkeypatch.setattr(ce, "_HAS_YFINANCE", True)
        monkeypatch.setattr(ce.yf, "Ticker", lambda t: _Ticker(_fin(rows)))
    return use


def test_annual_lines_carry_diluted_eps(stub):
    stub({"Total Revenue": [394.3e9, 383.3e9, 391.0e9, 416.2e9],
          "Net Income": [99.8e9, 97.0e9, 93.7e9, 112.0e9],
          "Gross Profit": [170.8e9, 169.1e9, 180.7e9, 195.2e9],
          "Diluted EPS": [6.11, 6.13, 6.08, 7.46]})
    out = ce.get_historical_financials("AAPL")
    assert "FY2023: Revenue $383.30B" in out
    assert "Diluted EPS $6.13" in out and "Diluted EPS $7.46" in out


def test_a_loss_per_share_keeps_its_sign(stub):
    stub({"Total Revenue": [66.6e9, 77.8e9, 66.5e9, 80.0e9],
          "Net Income": [-5.05e9, -2.22e9, -11.8e9, 2.0e9],
          "Diluted EPS": [-8.30, -3.67, -18.36, 2.48]})
    out = ce.get_historical_financials("BA")
    assert "Diluted EPS $-3.67" in out


def test_falls_back_to_basic_eps_then_says_not_available(stub):
    stub({"Total Revenue": [1e9] * 4, "Net Income": [1e8] * 4, "Basic EPS": [1.0, 1.1, 1.2, 1.3]})
    assert "Diluted EPS $1.20" in ce.get_historical_financials("X")
    stub({"Total Revenue": [1e9] * 4, "Net Income": [1e8] * 4})
    assert "Diluted EPS Not available" in ce.get_historical_financials("X")
