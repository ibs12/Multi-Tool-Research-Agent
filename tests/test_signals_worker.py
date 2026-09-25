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

from datetime import datetime

_NY = signals._MARKET_TZ


def _bars(*days_and_closes):
    """[(\"2026-09-21\", 100.0), ...] → what _daily_closes returns."""
    return [(datetime.combine(datetime.fromisoformat(d).date(), signals._MARKET_CLOSE, _NY), c)
            for d, c in days_and_closes]


def _patch_closes(monkeypatch, *days_and_closes):
    monkeypatch.setattr(signals, "_daily_closes", lambda *a, **k: _bars(*days_and_closes))


# Last Refresh ran Tue 2026-09-22 at 18:30 ET — after that day's close.
_RAN_AT = datetime(2026, 9, 22, 18, 30, tzinfo=_NY).isoformat()


def test_a_small_move_since_the_refresh_is_not_a_signal(monkeypatch):
    _patch_closes(monkeypatch, ("2026-09-22", 100.0), ("2026-09-23", 102.0))
    assert signals.price_signal("AAPL", _RAN_AT) is None


def test_a_large_move_since_the_refresh_is_a_signal(monkeypatch):
    _patch_closes(monkeypatch, ("2026-09-22", 100.0), ("2026-09-23", 88.0))
    sig = signals.price_signal("AAPL", _RAN_AT)
    assert sig["kind"] == "price" and sig["move_pct"] == pytest.approx(-12.0, abs=0.1)
    assert sig["reference_close"] == 100.0


def test_a_move_already_researched_does_not_fire_again(monkeypatch):
    """The bug this replaced: a +12.8% run-up BEFORE the Refresh fired on every
    sweep for five days, buying the same research again each time."""
    _patch_closes(monkeypatch, ("2026-09-16", 80.0), ("2026-09-18", 88.0),
                  ("2026-09-22", 90.2), ("2026-09-23", 90.5))
    assert signals.price_signal("AMD", _RAN_AT) is None


def test_the_reference_is_the_close_the_refresh_could_see(monkeypatch):
    # A Refresh at 11:00 ET saw the PREVIOUS day's close, so a same-day slide
    # after it is a move since the refresh, not part of the baseline.
    ran_midday = datetime(2026, 9, 22, 11, 0, tzinfo=_NY).isoformat()
    _patch_closes(monkeypatch, ("2026-09-21", 100.0), ("2026-09-22", 91.0))
    sig = signals.price_signal("AAPL", ran_midday)
    assert sig and sig["reference_close"] == 100.0


def test_no_close_since_the_refresh_is_not_a_signal(monkeypatch):
    _patch_closes(monkeypatch, ("2026-09-21", 50.0), ("2026-09-22", 100.0))
    assert signals.price_signal("AAPL", _RAN_AT) is None


def test_without_a_previous_refresh_price_has_no_baseline(monkeypatch):
    _patch_closes(monkeypatch, ("2026-09-22", 100.0), ("2026-09-23", 50.0))
    assert signals.price_signal("AAPL", None) is None


def test_a_dead_price_source_is_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr(signals, "_daily_closes", boom)
    assert signals.price_signal("AAPL", _RAN_AT) is None


def test_filing_wins_over_price(monkeypatch):
    monkeypatch.setattr(signals, "recent_filings", lambda *a, **k: _filings("acc-2"))
    _patch_closes(monkeypatch, ("2026-09-22", 100.0), ("2026-09-23", 80.0))
    sig = signals.detect({"cik": 320193, "ticker": "AAPL", "last_seen_accession": "acc-1"},
                         _RAN_AT)
    assert sig["kind"] == "filing"


def test_detect_measures_price_from_the_last_refresh(monkeypatch):
    monkeypatch.setattr(signals, "recent_filings", lambda *a, **k: _filings("acc-1"))
    _patch_closes(monkeypatch, ("2026-09-22", 100.0), ("2026-09-23", 110.0))
    sig = signals.detect({"cik": 320193, "ticker": "AAPL", "last_seen_accession": "acc-1"},
                         _RAN_AT)
    assert sig["kind"] == "price" and sig["move_pct"] == pytest.approx(10.0)


def test_the_worker_hands_detect_the_last_refresh_time(monkeypatch):
    _watch(monkeypatch, [{"company_key": "cik:1", "name": "AMD", "cik": 1,
                          "ticker": "AMD", "last_seen_accession": "acc-1"}])
    monkeypatch.setattr(watch_worker, "newest_accession", lambda cik: "acc-1")

    async def _history(*a, **k):
        return [{"id": "r1", "figures": {}, "created_at": _RAN_AT}]
    monkeypatch.setattr(watch_worker, "runs_for_company", _history)
    seen = []
    monkeypatch.setattr(watch_worker, "detect", lambda c, since: seen.append(since))
    _never_refresh(monkeypatch)

    asyncio.run(watch_worker.sweep(dry_run=True))
    assert seen == [_RAN_AT]


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
    monkeypatch.setattr(watch_worker, "detect", lambda c, *_: None)
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
                        lambda c, *_: pytest.fail("detect should not gate a baseline"))

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
                        lambda c, *_: {"kind": "filing", "detail": "10-Q filed 2026-07-31"})
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


def test_dry_run_does_not_consume_the_signal(monkeypatch):
    """A dry run that recorded the new filing as seen would make the real sweep
    say "no signal" — the change swallowed by the act of checking for it."""
    _watch(monkeypatch, [{"company_key": "cik:1", "name": "NVIDIA Corp", "cik": 1,
                          "ticker": None, "last_seen_accession": "acc-1"}])
    monkeypatch.setattr(watch_worker, "detect", lambda c, *_: {"kind": "filing", "detail": "x"})
    monkeypatch.setattr(watch_worker, "newest_accession", lambda cik: "acc-2")

    async def _history(*a, **k):
        return [{"id": "r1", "figures": {}}]
    monkeypatch.setattr(watch_worker, "runs_for_company", _history)
    _never_refresh(monkeypatch)

    writes = []

    async def _spy_set_last_seen(*a, **k):
        writes.append(a)
    monkeypatch.setattr(watch_worker, "set_last_seen", _spy_set_last_seen)

    asyncio.run(watch_worker.sweep(dry_run=True))
    assert writes == [], "a dry run must not mark the pending filing as seen"
    assert asyncio.run(run_store.recent_sweeps()) == [], \
        "a dry run must not look like a cron heartbeat"


def test_dry_run_spends_nothing(monkeypatch):
    _watch(monkeypatch, [{"company_key": "cik:1", "name": "NVIDIA Corp", "cik": 1,
                          "ticker": None, "last_seen_accession": "acc-1"}])
    monkeypatch.setattr(watch_worker, "detect", lambda c, *_: {"kind": "filing", "detail": "x"})
    monkeypatch.setattr(watch_worker, "newest_accession", lambda cik: "acc-2")

    async def _history(*a, **k):
        return [{"id": "r1", "figures": {}}]
    monkeypatch.setattr(watch_worker, "runs_for_company", _history)
    _never_refresh(monkeypatch)

    summary = asyncio.run(watch_worker.sweep(dry_run=True))
    assert summary["refreshed"] == 0 and summary["dry_run"] is True


def test_a_sweep_is_recorded_on_postgres_too(monkeypatch):
    """asyncpg binds timestamptz only from datetime objects. An ISO string
    raised DataError, record_sweep swallowed it, and production /sweeps stayed
    empty — the dead-cron ambiguity the sweep log exists to remove."""
    from datetime import datetime

    monkeypatch.setattr(run_store, "_pg_url", lambda: "postgresql://stub")
    monkeypatch.setattr(run_store, "_schema_ready", True)
    bound = []

    async def _fake_pg(query, *args, fetch="none"):
        if "INSERT INTO" in query and run_store._SWEEPS in query:
            for a in args[2:4]:                     # started_at, finished_at
                if not isinstance(a, datetime):
                    raise ValueError(f"expected a datetime, got {type(a).__name__}")
            bound.append(args)
    monkeypatch.setattr(run_store, "_pg", _fake_pg)

    sweep_id = asyncio.run(run_store.record_sweep(
        3, 1, 0, started_at="2026-09-23T18:00:00+00:00"))
    assert sweep_id is not None and len(bound) == 1
