"""
tests/test_run_store.py
-----------------------
Run persistence and the permalink endpoint. Offline: the graph and synthesis are
mocked, and the store runs on its SQLite backend (conftest pins PGVECTOR_URL
empty), so no network, no LLM, no Postgres.

Covers the two terminal outcomes a permalink has to survive: a cleared run with
a brief, and an escalated run with no brief but an escalation package.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import api.main as api_main  # noqa: E402
from api import run_store  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Point the SQLite store at a temp file per test."""
    monkeypatch.setattr(run_store, "_SQLITE_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(run_store, "_schema_ready", False)
    yield


class _FakeGraph:
    def __init__(self, state):
        self._state = state

    async def ainvoke(self, _initial):
        return self._state


def _mock_run(monkeypatch, state, report="## Executive Summary\nAll good."):
    monkeypatch.setattr(api_main, "graph", _FakeGraph(state))
    monkeypatch.setattr(api_main, "synthesis_node",
                        lambda _s: {"final_report": report, "error": None})


# ── store round-trip ──────────────────────────────────────────────────────────

def test_save_and_get_round_trip():
    async def go():
        rid = await run_store.save_run({"query": "q", "agent_mode": "multi",
                                        "final_report": "# hi", "termination_reason": "completed"})
        assert rid
        got = await run_store.get_run(rid)
        assert got["final_report"] == "# hi"
        assert got["query"] == "q"
        assert got["created_at"]          # stamped on save
        return rid
    asyncio.run(go())


def test_missing_run_returns_none():
    assert asyncio.run(run_store.get_run("does-not-exist")) is None


def test_ids_are_unguessable_and_unique():
    ids = {run_store.new_run_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) >= 10 for i in ids)


# ── API: a cleared run is saved and replayable ────────────────────────────────

def test_cleared_run_is_persisted_and_replayable(monkeypatch):
    _mock_run(monkeypatch, {
        "company_target": "Apple Inc. (AAPL)", "termination_reason": "completed",
        "tools_called": ["web_search"], "iteration_count": 2, "handoff": {},
    })
    client = TestClient(api_main.app)
    resp = client.post("/research", json={"query": "Analyse Apple Inc.", "agent_mode": "multi"})
    assert resp.status_code == 200
    body = resp.json()
    run_id = body["run_id"]
    assert run_id, "a cleared run should get a permalink id"

    replay = client.get(f"/runs/{run_id}")
    assert replay.status_code == 200
    rec = replay.json()
    assert rec["final_report"].startswith("## Executive Summary")
    assert rec["agent_mode"] == "multi"
    assert rec["company_target"] == "Apple Inc. (AAPL)"


# ── API: an escalated run is saved too (no brief, but a package) ──────────────

def test_escalated_run_is_persisted_with_its_package(monkeypatch):
    package = {"reason": "cannot verify the entity", "unresolved": ["confirm the ticker"]}
    _mock_run(monkeypatch, {
        "company_target": "Zephyr Dynamics Corporation",
        "termination_reason": "escalated",
        "tools_called": ["web_search"], "iteration_count": 3,
        "handoff": {"escalation": package,
                    "compliance_verdict": {"verdict": "escalate",
                                           "gap_type": "unverifiable_citation"}},
    })
    client = TestClient(api_main.app)
    body = client.post("/research", json={"query": "Analyse Zephyr Dynamics Corporation"}).json()
    assert body["final_report"] == ""            # escalation ships no brief
    assert body["escalation"]["reason"] == "cannot verify the entity"

    rec = client.get(f"/runs/{body['run_id']}").json()
    assert rec["termination_reason"] == "escalated"
    assert rec["escalation"]["unresolved"] == ["confirm the ticker"]
    assert rec["compliance_verdict"]["verdict"] == "escalate"


def test_unknown_permalink_404s():
    client = TestClient(api_main.app)
    assert client.get("/runs/nope-nope").status_code == 404


def test_a_storage_failure_does_not_fail_the_run(monkeypatch):
    """Saving is best-effort: a broken store must not break research."""
    _mock_run(monkeypatch, {"termination_reason": "completed", "handoff": {}})

    async def _boom(*_a, **_k):
        raise RuntimeError("store is down")
    monkeypatch.setattr(run_store, "_sqlite_save_sync",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("store is down")))

    client = TestClient(api_main.app)
    resp = client.post("/research", json={"query": "Analyse Apple Inc."})
    assert resp.status_code == 200
    assert resp.json()["run_id"] is None          # no permalink, but the run succeeded
    assert resp.json()["final_report"]
