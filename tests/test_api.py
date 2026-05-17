"""
tests/test_api.py
-----------------
Smoke tests for both API endpoints.
Run with the server already started in another terminal:

    # Terminal 1
    python server.py

    # Terminal 2
    python tests/test_api.py
"""

from __future__ import annotations

import json
import sys
import httpx

BASE_URL = "http://localhost:8000"
TEST_QUERY = "Analyse Apple Inc. investment outlook"


def test_health():
    print("Testing /health ...")
    r = httpx.get(f"{BASE_URL}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    print("  ✓ Health check passed\n")


def test_batch():
    print("Testing POST /research (batch) ...")
    r = httpx.post(
        f"{BASE_URL}/research",
        json={"query": TEST_QUERY, "max_iterations": 4},
        timeout=120,
    )
    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    data = r.json()
    assert data["final_report"], "final_report is empty"
    assert data["tools_called"], "no tools were called"
    print(f"  ✓ Batch endpoint returned report ({len(data['final_report'])} chars)")
    print(f"  ✓ Tools called: {data['tools_called']}")
    print(f"  ✓ Iterations:   {data['iteration_count']}")
    print(f"  ✓ Elapsed:      {data['elapsed_seconds']}s\n")


def test_stream():
    print("Testing POST /research/stream (SSE) ...")
    events = []

    with httpx.stream(
        "POST",
        f"{BASE_URL}/research/stream",
        json={"query": TEST_QUERY, "max_iterations": 4},
        timeout=120,
    ) as r:
        assert r.status_code == 200, f"Got {r.status_code}"
        for line in r.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                events.append(payload)
                event_type = payload.get("event")
                if event_type == "node_complete":
                    print(f"  → node: {payload.get('node')}")
                elif event_type == "report":
                    print(f"  → report received ({len(payload.get('data',''))} chars)")
                elif event_type == "done":
                    print("  → stream closed")
                elif event_type == "error":
                    print(f"  ✗ ERROR: {payload.get('data')}")

    event_types = [e.get("event") for e in events]
    assert "report" in event_types, "No report event received"
    assert "done" in event_types, "Stream did not close cleanly"
    print("  ✓ SSE stream completed successfully\n")


if __name__ == "__main__":
    try:
        test_health()
        test_batch()
        test_stream()
        print("All tests passed ✓")
    except AssertionError as e:
        print(f"\nTest failed: {e}")
        sys.exit(1)
    except httpx.ConnectError:
        print("Could not connect to server. Is it running? (python server.py)")
        sys.exit(1)