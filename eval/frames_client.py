"""
eval/frames_client.py
─────────────────────
Compatibility shim. The SEC client moved to `agent/sec_client.py` when the
watchlist began using it as a change-detector (ADR-0011): a domain capability
used by the product must not live in the test harness — see CLAUDE.md on the
dependency direction. Re-exported here under the names the eval already uses.
"""

from agent.sec_client import (  # noqa: F401
    USER_AGENT,
    companyfacts,
    concept_entries,
    frames,
    get_json,
    recent_filings,
    submissions,
)
