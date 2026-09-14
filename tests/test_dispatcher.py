"""
tests/test_dispatcher.py
------------------------
Structural tool-ordering (PREREQUISITES) in the async dispatcher — issue #5,
ADR-0002. Tools are faked so the ordering logic is tested in isolation (no
network, no real vector store). Each fake records the state it saw so we can
assert that a dependent tool observes its prerequisite's fresh output.

Run with:
    pytest tests/test_dispatcher.py -v
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from agent.graph import async_tool_dispatcher


def _result(name: str, success: bool = True, output: str = "ok") -> dict:
    return {"tool_name": name, "query": "", "output": output,
            "success": success, "error": None if success else output}


def _fake_registry(seen: dict) -> dict:
    async def sec_edgar(state):
        seen["sec_edgar_ran"] = True
        return {"tool_results": [_result("sec_edgar")], "tools_called": ["sec_edgar"]}

    async def rag_search(state):
        # Record which tool results rag_search could see when it ran.
        seen["rag_saw"] = [r["tool_name"] for r in state.get("tool_results", [])]
        return {"tool_results": [_result("rag_search")], "tools_called": ["rag_search"]}

    async def web_search(state):
        return {"tool_results": [_result("web_search")], "tools_called": ["web_search"]}

    return {"sec_edgar": sec_edgar, "rag_search": rag_search, "web_search": web_search}


def _dispatch(state, seen):
    with patch.dict("agent.graph.TOOL_REGISTRY", _fake_registry(seen), clear=True):
        return asyncio.run(async_tool_dispatcher(state))


def _errors(out):
    return [r for r in out["tool_results"] if not r["success"]]


def test_co_scheduled_runs_prereq_first_and_threads_output():
    # rag_search listed BEFORE sec_edgar to prove ordering isn't positional.
    seen = {}
    out = _dispatch(
        {"tools_remaining": ["rag_search", "sec_edgar"], "tool_results": [], "tools_called": []},
        seen,
    )
    # rag_search saw sec_edgar's freshly-produced result (waved after it).
    assert "sec_edgar" in seen["rag_saw"]
    names = [r["tool_name"] for r in out["tool_results"]]
    assert names.count("sec_edgar") == 1 and names.count("rag_search") == 1
    assert _errors(out) == []


def test_missing_prereq_is_auto_injected():
    # rag_search queued alone, sec_edgar never ran → sec_edgar injected + waved first.
    seen = {}
    out = _dispatch(
        {"tools_remaining": ["rag_search"], "tool_results": [], "tools_called": []},
        seen,
    )
    assert seen.get("sec_edgar_ran") is True
    assert "sec_edgar" in seen["rag_saw"]
    assert _errors(out) == []


def test_failed_prereq_drops_dependent_with_loud_error():
    # sec_edgar ran earlier and FAILED → rag_search must be dropped, not run on bad data.
    seen = {}
    prior = [_result("sec_edgar", success=False, output="[SEC EDGAR Error] boom")]
    out = _dispatch(
        {"tools_remaining": ["rag_search"], "tool_results": prior, "tools_called": ["sec_edgar"]},
        seen,
    )
    assert "rag_saw" not in seen                       # rag node never executed
    errs = _errors(out)
    assert any(r["tool_name"] == "rag_search" and "Dispatcher Error" in r["output"] for r in errs)


def test_satisfied_prereq_lets_dependent_run_immediately():
    # sec_edgar already succeeded in a prior iteration → rag_search runs, no error.
    seen = {}
    prior = [_result("sec_edgar", success=True)]
    out = _dispatch(
        {"tools_remaining": ["rag_search"], "tool_results": prior, "tools_called": ["sec_edgar"]},
        seen,
    )
    names = [r["tool_name"] for r in out["tool_results"]]
    assert "rag_search" in names
    assert _errors(out) == []
