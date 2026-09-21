"""
tests/test_signals_worker.py
----------------------------
Phase 2 (#43): Signals gate Refreshes, and a sweep leaves evidence it ran.

Offline — SEC, yfinance, the notifier and the agent itself are all mocked, so
this exercises the decision logic rather than the network. The invariants under
test are the ones that make the design honest (ADR-0011):

  * no signal ⇒ no Refresh ⇒ no spend
  * a sweep is recorded EVEN when nothing changed, so silence is not ambiguous
  * a signal source that is down degrades to "no signal", never an exception
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from agent import notify as notify_mod
from agent import signals
from api import run_store

import watch_worker


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(run_store, "_SQLITE_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(run_store, "_schema_ready", False)
    yield


def _filings(*accessions):
    return [{"accession": a, "form": "10-Q", "filed_at": "2026-07-31",
             "period": "2026-06-27"} for a in accessions]


# ── filing signal ─────────────────────────────────────────────────────────────

def test_no_new_filing_means_no_signal(monkeypatch):
    monkeypatch.setattr(signals, "recent_filings", lambda *a, **k: _filings("acc-1"))
    assert signals.filing_signal(320193, last_seen_accession="acc-1") is None


def test_a_new_accession_fires(monkeypatch):
    monkeypatch.setattr(signals, "recent_filings", lambda *a, **k: _filings("acc-2"))
    sig = signals.filing_signal(320193, last_seen_accession="acc-1")
    assert sig["kind"] == "filing" and sig["accession"] == "acc-2"
    assert "10-Q" in sig["detail"]


def test_without_a_baseline_nothing_is_provably_new(monkeypatch):
    # First observation establishes the baseline; it is not evidence of change.
    monkeypatch.setattr(signals, "recent_filings", lambda *a, **k: _filings("acc-1"))
    assert signals.filing_signal(320193, last_seen_accession=None) is None


def test_a_dead_signal_source_is_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("SEC unreachable")
    monkeypatch.setattr(signals, "recent_filings", boom)
    assert signals.filing_signal(320193, "acc-1") is None
    assert signals.newest_accession(320193) is None


# ── price signal ──────────────────────────────────────────────────────────────

class _FakeYF:
    def __init__(self, closes):
        self._closes = closes

    def Ticker(self, _t):
        closes = self._closes

        class _T:
            def history(self, period=None):
                return {"Close": _Series(closes)}
        return _T()


class _Series(list):
    def tolist(self):
        return list(self)


def _patch_yf(monkeypatch, closes):
    monkeypatch.setitem(sys.modules, "yfinance", _FakeYF(closes))


def test_a_small_move_is_not_a_signal(monkeypatch):
    _patch_yf(monkeypatch, [100.0, 101.0, 102.0])       # +2%
    assert signals.price_signal("AAPL", threshold_pct=7.0) is None


def test_a_large_move_is_a_signal(monkeypatch):
    _patch_yf(monkeypatch, [100.0, 95.0, 88.0])         # -12%
    sig = signals.price_signal("AAPL", threshold_pct=7.0)
    assert sig["kind"] == "price" and sig["move_pct"] == pytest.approx(-12.0, abs=0.1)


def test_filing_wins_over_price(monkeypatch):
    monkeypatch.setattr(signals, "recent_filings", lambda *a, **k: _filings("acc-2"))
    _patch_yf(monkeypatch, [100.0, 80.0])
    sig = signals.detect({"cik": 320193, "ticker": "AAPL", "last_seen_accession": "acc-1"})
    assert sig["kind"] == "filing"


# ── notifier ──────────────────────────────────────────────────────────────────

def test_notify_is_a_silent_no_op_when_unconfigured(monkeypatch):
    monkeypatch.delenv("WATCH_WEBHOOK_URL", raising=False)
    assert notify_mod.notify("anything") is False
    assert notify_mod.is_configured() is False


def test_notify_never_raises_on_a_dead_webhook(monkeypatch):
    monkeypatch.setenv("WATCH_WEBHOOK_URL", "https://example.invalid/hook")
    monkeypatch.setattr(notify_mod.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    assert notify_mod.notify("still fine") is False


# ── the sweep ─────────────────────────────────────────────────────────────────

def _watch(monkeypatch, companies):
    async def _list(*a, **k):
        return companies
    monkeypatch.setattr(watch_worker, "list_watchlist", _list)


def _never_refresh(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("a Refresh was spent when no Signal fired")
    monkeypatch.setattr(watch_worker, "_refresh", _boom)


def test_quiet_company_costs_nothing_but_the_sweep_is_still_recorded(monkeypatch):
    _watch(monkeypatch, [{"company_key": "cik:320193", "name": "Apple Inc.",
                          "cik": 320193, "ticker": None, "last_seen_accession": "acc-1"}])
    monkeypatch.setattr(watch_worker, "detect", lambda c: None)
    monkeypatch.setattr(watch_worker, "newest_accession", lambda cik: "acc-1")

    async def _history(*a, **k):
        return [{"id": "r1", "figures": {}}]          # already has a baseline run
    monkeypatch.setattr(watch_worker, "runs_for_company", _history)
    _never_refresh(monkeypatch)

    summary = asyncio.run(watch_worker.sweep())
    assert summary["refreshed"] == 0 and summary["notified"] == 0
    assert summary["checked"] == 1

    # ADR-0011: silence must be distinguishable from a dead cron.
    sweeps = asyncio.run(run_store.recent_sweeps())
    assert len(sweeps) == 1 and sweeps[0]["checked"] == 1 and sweeps[0]["refreshed"] == 0


def test_a_company_with_no_runs_gets_a_baseline_refresh(monkeypatch):
    _watch(monkeypatch, [{"company_key": "cik:1", "name": "NewCo", "cik": 1,
                          "ticker": None, "last_seen_accession": None}])
    monkeypatch.setattr(watch_worker, "newest_accession", lambda cik: "acc-1")
    monkeypatch.setattr(watch_worker, "detect",
                        lambda c: pytest.fail("detect should not gate a baseline"))

    async def _history(*a, **k):
        return []
    monkeypatch.setattr(watch_worker, "runs_for_company", _history)

    calls = []

    async def _fake_refresh(company, signal, previous):
        calls.append(signal["kind"])
        return {"termination_reason": "completed", "figures": {}}, "run-1"
    monkeypatch.setattr(watch_worker, "_refresh", _fake_refresh)
    monkeypatch.setattr(watch_worker, "notify", lambda *a, **k: True)

    summary = asyncio.run(watch_worker.sweep())
    assert calls == ["baseline"] and summary["refreshed"] == 1


def test_a_signal_refreshes_once_and_notifies_on_material_change(monkeypatch):
    _watch(monkeypatch, [{"company_key": "cik:1", "name": "NVIDIA Corp", "cik": 1,
                          "ticker": "NVDA", "last_seen_accession": "acc-1"}])
    monkeypatch.setattr(watch_worker, "detect",
                        lambda c: {"kind": "filing", "detail": "10-Q filed 2026-07-31"})
    monkeypatch.setattr(watch_worker, "newest_accession", lambda cik: "acc-2")

    async def _history(*a, **k):
        return [{"id": "r1", "figures": {"FY2027E": {"eps": 9.58}},
                 "compliance_verdict": {"verdict": "clear"}}]
    monkeypatch.setattr(watch_worker, "runs_for_company", _history)

    refreshes = []

    async def _fake_refresh(company, signal, previous):
        refreshes.append(company["company_key"])
        return ({"termination_reason": "completed",
                 "figures": {"FY2027E": {"eps": 11.20}},
                 "compliance_verdict": {"verdict": "clear"}}, "run-2")
    monkeypatch.setattr(watch_worker, "_refresh", _fake_refresh)

    sent = []
    monkeypatch.setattr(watch_worker, "notify",
                        lambda text, link=None: sent.append(text) or True)

    summary = asyncio.run(watch_worker.sweep())
    assert refreshes == ["cik:1"], "exactly one Refresh"
    assert summary["notified"] == 1
    assert "eps" in sent[0] and "NVIDIA" in sent[0]


def test_dry_run_spends_nothing(monkeypatch):
    _watch(monkeypatch, [{"company_key": "cik:1", "name": "NVIDIA Corp", "cik": 1,
                          "ticker": None, "last_seen_accession": "acc-1"}])
    monkeypatch.setattr(watch_worker, "detect", lambda c: {"kind": "filing", "detail": "x"})
    monkeypatch.setattr(watch_worker, "newest_accession", lambda cik: "acc-2")

    async def _history(*a, **k):
        return [{"id": "r1", "figures": {}}]
    monkeypatch.setattr(watch_worker, "runs_for_company", _history)
    _never_refresh(monkeypatch)

    summary = asyncio.run(watch_worker.sweep(dry_run=True))
    assert summary["refreshed"] == 0 and summary["dry_run"] is True
