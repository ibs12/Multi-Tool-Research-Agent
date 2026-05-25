"""
tests/test_supervisor.py
------------------------
Unit tests for supervisor parse logic and LangGraph routing.

All Anthropic API calls are mocked — no real network traffic.

Run with:
    pytest tests/test_supervisor.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.graph import route_after_dispatcher, route_after_supervisor
from langgraph.graph import END


# ── Routing: route_after_supervisor ──────────────────────────────────────────

def test_routes_to_dispatcher_when_tools_queued():
    state = {"tools_remaining": ["web_search", "wikipedia"], "iteration_count": 1}
    assert route_after_supervisor(state) == "dispatcher"


def test_routes_to_end_when_no_tools():
    state = {"tools_remaining": [], "iteration_count": 1}
    assert route_after_supervisor(state) == END


def test_routes_to_end_when_tools_remaining_missing():
    state = {"iteration_count": 1}
    assert route_after_supervisor(state) == END


# ── Routing: route_after_dispatcher ──────────────────────────────────────────

def test_dispatcher_routes_to_supervisor_under_max():
    state = {"iteration_count": 3, "max_iterations": 8}
    assert route_after_dispatcher(state) == "supervisor"


def test_dispatcher_routes_to_end_at_max():
    state = {"iteration_count": 8, "max_iterations": 8}
    assert route_after_dispatcher(state) == END


def test_dispatcher_routes_to_end_over_max():
    state = {"iteration_count": 10, "max_iterations": 8}
    assert route_after_dispatcher(state) == END


def test_dispatcher_uses_default_max_when_missing():
    # Default max_iterations = 8 in make_initial_state
    state = {"iteration_count": 7}
    assert route_after_dispatcher(state) == "supervisor"


# ── Supervisor node: never re-queues already-called tools ─────────────────────

def _make_plan_block(tools: list[str], ready: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        type="tool_use",
        name="plan_research",
        input={
            "company_target": "Apple Inc. (AAPL)",
            "reasoning": "Need more data.",
            "tools_to_call": tools,
            "ready_to_synthesise": ready,
        },
    )


def _make_api_response(block) -> MagicMock:
    resp = MagicMock()
    resp.content = [block]
    return resp


@patch("agent.nodes.supervisor.anthropic.Anthropic")
def test_supervisor_filters_already_called_tools(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _make_api_response(
        _make_plan_block(["web_search", "wikipedia", "arxiv"])
    )

    from agent.nodes.supervisor import supervisor_node

    state = {
        "query": "Analyse Apple Inc.",
        "tools_called": ["web_search", "wikipedia"],
        "tool_results": [],
        "iteration_count": 1,
        "max_iterations": 8,
    }
    result = supervisor_node(state)

    # web_search and wikipedia should be stripped — already called
    assert "web_search" not in result["tools_remaining"]
    assert "wikipedia" not in result["tools_remaining"]
    assert "arxiv" in result["tools_remaining"]


@patch("agent.nodes.supervisor.anthropic.Anthropic")
def test_supervisor_increments_iteration_count(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _make_api_response(
        _make_plan_block(["arxiv"])
    )

    from agent.nodes.supervisor import supervisor_node

    state = {
        "query": "Analyse Apple Inc.",
        "tools_called": [],
        "tool_results": [],
        "iteration_count": 2,
        "max_iterations": 8,
    }
    result = supervisor_node(state)
    assert result["iteration_count"] == 3


@patch("agent.nodes.supervisor.anthropic.Anthropic")
def test_supervisor_force_injects_rag_search_after_sec_edgar(mock_anthropic_cls):
    """rag_search must be queued in the iteration immediately after sec_edgar."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    # Claude doesn't mention rag_search but sec_edgar already ran
    mock_client.messages.create.return_value = _make_api_response(
        _make_plan_block(["arxiv"], ready=False)
    )

    from agent.nodes.supervisor import supervisor_node

    state = {
        "query": "Analyse Apple Inc.",
        "tools_called": ["web_search", "wikipedia", "sec_edgar"],
        "tool_results": [],
        "iteration_count": 3,
        "max_iterations": 8,
    }
    result = supervisor_node(state)

    assert "rag_search" in result["tools_remaining"]


@patch("agent.nodes.supervisor.anthropic.Anthropic")
def test_supervisor_does_not_inject_rag_search_if_already_called(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _make_api_response(
        _make_plan_block(["arxiv"], ready=False)
    )

    from agent.nodes.supervisor import supervisor_node

    state = {
        "query": "Analyse Apple Inc.",
        "tools_called": ["web_search", "wikipedia", "sec_edgar", "rag_search"],
        "tool_results": [],
        "iteration_count": 4,
        "max_iterations": 8,
    }
    result = supervisor_node(state)

    assert "rag_search" not in result["tools_remaining"]


@patch("agent.nodes.supervisor.anthropic.Anthropic")
def test_supervisor_ready_to_synthesise_empties_tools(mock_anthropic_cls):
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.return_value = _make_api_response(
        _make_plan_block(["arxiv"], ready=True)
    )

    from agent.nodes.supervisor import supervisor_node

    state = {
        "query": "Analyse Apple Inc.",
        "tools_called": ["web_search", "wikipedia", "sec_edgar", "rag_search"],
        "tool_results": [],
        "iteration_count": 5,
        "max_iterations": 8,
    }
    result = supervisor_node(state)

    assert result["tools_remaining"] == []


@patch("agent.nodes.supervisor.anthropic.Anthropic")
def test_supervisor_handles_api_error_gracefully(mock_anthropic_cls):
    import anthropic as _anthropic

    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_client.messages.create.side_effect = _anthropic.APIError(
        message="Rate limit exceeded",
        request=MagicMock(),
        body=None,
    )

    from agent.nodes.supervisor import supervisor_node

    state = {
        "query": "Analyse Apple Inc.",
        "tools_called": [],
        "tool_results": [],
        "iteration_count": 0,
        "max_iterations": 8,
    }
    result = supervisor_node(state)

    # Should not raise — should return a safe fallback
    assert "error" in result
    assert result["tools_remaining"] == []


@patch("agent.nodes.supervisor.anthropic.Anthropic")
def test_supervisor_fallback_when_no_plan_block(mock_anthropic_cls):
    """If Claude returns no plan_research block, agent should still terminate cleanly."""
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client

    # Response with no tool_use blocks at all
    resp = MagicMock()
    resp.content = []
    mock_client.messages.create.return_value = resp

    from agent.nodes.supervisor import supervisor_node

    state = {
        "query": "Analyse Apple Inc.",
        "tools_called": ["web_search"],
        "tool_results": [],
        "iteration_count": 1,
        "max_iterations": 8,
    }
    result = supervisor_node(state)

    # Fallback plan sets ready_to_synthesise=True → empty tools_remaining
    assert result["tools_remaining"] == []
