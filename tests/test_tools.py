"""
tests/test_tools.py
-------------------
Tool-node contract tests. Nodes must return *deltas* — just the result they
produced — and let the append_list reducer accumulate. Returning the full
prior list double-counts it once the dispatcher merge + reducer both apply
(issue #2). Nodes must also not return tools_remaining (the dispatcher owns it).

Run with:
    pytest tests/test_tools.py -v
"""

from __future__ import annotations

from unittest.mock import patch

from agent.state import append_list
from agent.nodes.tools import wikipedia_node


def _state_with_prior():
    return {
        "query": "Apple",
        "company_target": "Apple Inc. (AAPL)",
        "tool_results": [
            {"tool_name": "web_search", "query": "x", "output": "prior",
             "success": True, "error": None},
        ],
        "tools_called": ["web_search"],
        "tools_remaining": ["wikipedia"],
        "financial_context": {},
    }


@patch("agent.nodes.tools.run_wikipedia")
def test_wikipedia_node_returns_delta_only(mock_wiki):
    mock_wiki.return_value = "Apple Inc. is a technology company."
    out = wikipedia_node(_state_with_prior())

    # Just this tool's one new result — not the accumulated list.
    assert len(out["tool_results"]) == 1
    assert out["tool_results"][0]["tool_name"] == "wikipedia"
    assert out["tools_called"] == ["wikipedia"]
    # The dispatcher owns tools_remaining; nodes must not return it.
    assert "tools_remaining" not in out


@patch("agent.nodes.tools.run_wikipedia")
def test_no_duplication_through_reducer(mock_wiki):
    # Reproduces the fix for issue #2: prior results must appear exactly once
    # after the node's delta is merged via the append_list reducer.
    mock_wiki.return_value = "Apple Inc. is a technology company."
    state = _state_with_prior()

    out = wikipedia_node(state)
    merged = append_list(state["tool_results"], out["tool_results"])

    names = [r["tool_name"] for r in merged]
    assert names == ["web_search", "wikipedia"]   # no duplicated web_search
