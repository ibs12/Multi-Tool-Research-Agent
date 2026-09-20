"""
tests/test_watchlist.py
-----------------------
Phase 1 (#42): the watchlist spine — company identity, stored figures, and
deltas computed with the eval's tolerances as the materiality filter (ADR-0012).

Offline: SQLite store (conftest pins PGVECTOR_URL empty), no network, no LLM.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.brief_parser import extract_all_periods
from agent.deltas import compute_delta, summarise_delta
from agent.materiality import is_material_change
from api import run_store

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(run_store, "_SQLITE_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(run_store, "_schema_ready", False)
    yield


def _run(figures, verdict="clear", flags=(), company="Apple Inc. (AAPL)"):
    return {
        "company_target": company,
        "company_key": run_store.company_key_for(company),
        "figures": figures,
        "compliance_verdict": {"verdict": verdict},
        "risk_assessment": {"red_flags": list(flags)},
    }


# ── materiality: the tolerance boundary ───────────────────────────────────────

def test_change_just_inside_tolerance_is_not_material():
    # ±0.5% on level figures — presentation rounding must not read as news.
    assert not is_material_change("revenue", 391_000_000_000, 392_000_000_000)  # 0.26%
    assert not is_material_change("gross_margin", 46.2, 46.25)                  # 0.05pp
    assert not is_material_change("eps", 6.13, 6.14)                            # 1 cent


def test_change_just_outside_tolerance_is_material():
    assert is_material_change("revenue", 391_000_000_000, 400_000_000_000)      # 2.3%
    assert is_material_change("gross_margin", 46.2, 46.5)                       # 0.3pp
    assert is_material_change("eps", 6.13, 6.50)


def test_appearing_or_vanishing_figure_is_never_a_change():
    # A field the parser missed is unknown, not movement (ADR-0012).
    assert not is_material_change("revenue", None, 391_000_000_000)
    assert not is_material_change("revenue", 391_000_000_000, None)


# ── deltas ────────────────────────────────────────────────────────────────────

def test_identical_runs_produce_an_empty_delta():
    figs = {"FY2025": {"revenue": 416_160_000_000, "eps": 7.10}}
    delta = compute_delta(_run(figs), _run(dict(figs)))
    assert delta["is_empty"]
    assert delta["figure_changes"] == []


def test_material_move_is_reported_with_direction():
    before = _run({"FY2027E": {"eps": 9.58}})
    after = _run({"FY2027E": {"eps": 11.20}})
    delta = compute_delta(before, after)
    assert not delta["is_empty"]
    change = delta["figure_changes"][0]
    assert change["field"] == "eps" and change["period"] == "FY2027E"
    assert change["pct_change"] == pytest.approx(16.9, abs=0.2)


def test_a_field_only_one_run_could_read_is_omitted_not_reported():
    before = _run({"FY2025": {"revenue": 416_160_000_000}})          # no margin read
    after = _run({"FY2025": {"revenue": 416_160_000_000, "gross_margin": 46.9}})
    delta = compute_delta(before, after)
    assert delta["is_empty"], "an unreadable-then-readable field is not a change"


def test_verdict_and_risk_flag_changes_surface():
    before = _run({}, verdict="clear", flags=["a", "b", "c"])
    after = _run({}, verdict="escalate", flags=["a", "b", "c", "d", "e"])
    delta = compute_delta(before, after)
    assert delta["verdict_change"] == {"before": "clear", "after": "escalate"}
    assert delta["red_flag_change"] == {"before": 3, "after": 5}
    assert "escalate" in summarise_delta(delta, "NVDA")


def test_first_ever_run_is_a_baseline_not_news():
    delta = compute_delta(None, _run({"FY2025": {"revenue": 1}}))
    assert delta["is_empty"]


# ── company identity ──────────────────────────────────────────────────────────

def test_ticker_suffix_does_not_split_a_company():
    assert run_store.company_key_for("Apple Inc. (AAPL)") == run_store.company_key_for("Apple Inc.")


def test_cik_is_the_preferred_identity():
    # Survives renames and ticker changes.
    assert run_store.company_key_for("Whatever Corp", cik=320193) == "cik:320193"


# ── store + API ───────────────────────────────────────────────────────────────

def test_watchlist_add_list_remove_round_trip():
    async def go():
        await run_store.add_to_watchlist("Apple Inc.", cik=320193, ticker="AAPL")
        listed = await run_store.list_watchlist()
        assert [c["name"] for c in listed] == ["Apple Inc."]
        assert listed[0]["company_key"] == "cik:320193"
        await run_store.remove_from_watchlist("cik:320193")
        assert await run_store.list_watchlist() == []
    asyncio.run(go())


def test_adding_the_same_company_twice_is_idempotent():
    async def go():
        await run_store.add_to_watchlist("Apple Inc. (AAPL)")
        await run_store.add_to_watchlist("Apple Inc.")
        assert len(await run_store.list_watchlist()) == 1
    asyncio.run(go())


def test_company_endpoint_returns_timeline_and_delta():
    async def seed():
        await run_store.save_run(_run({"FY2027E": {"eps": 9.58}}))
        await run_store.save_run(_run({"FY2027E": {"eps": 11.20}}))
    asyncio.run(seed())

    client = TestClient(api_main.app)
    key = run_store.company_key_for("Apple Inc. (AAPL)")
    body = client.get(f"/companies/{key}").json()
    assert len(body["runs"]) == 2
    assert body["delta"]["figure_changes"][0]["field"] == "eps"


def test_watchlist_endpoints():
    client = TestClient(api_main.app)
    created = client.post("/watchlist", json={"name": "NVIDIA Corp", "ticker": "NVDA"})
    assert created.status_code == 200
    key = created.json()["company_key"]

    listed = client.get("/watchlist").json()
    assert [c["name"] for c in listed] == ["NVIDIA Corp"]
    assert listed[0]["latest_run"] is None        # tracked, never run yet
    assert listed[0]["delta"] is None

    assert client.delete(f"/watchlist/{key}").status_code == 200
    assert client.get("/watchlist").json() == []


# ── figures come from the brief the agent actually shipped ────────────────────

def test_figures_are_read_from_the_shipped_snapshot_table():
    brief = (
        "## Financial Snapshot\n"
        "| Metric | FY2024 | FY2025 | FY2026E |\n"
        "|---|---|---|---|\n"
        "| Revenue | $391.04B¹ | $416.16B¹ | $477.83B³ |\n"
        "| Gross Margin % | 46.2%¹ | 46.9%¹ | — |\n"
        "| EPS | — | 7.10¹ | 8.82³ |\n"
    )
    figs = extract_all_periods(brief)
    assert figs["FY2025"]["revenue"] == pytest.approx(416_160_000_000)
    assert figs["FY2025"]["gross_margin"] == pytest.approx(46.9)
    assert "eps" not in figs["FY2024"], "an em-dash cell is unknown, not zero"
    assert figs["FY2026E"]["eps"] == pytest.approx(8.82)
