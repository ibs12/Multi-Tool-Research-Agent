"""
agent/notify.py
───────────────
The nudge. In-app is the source of truth; this just points at it (ADR-0011).

A webhook is one environment variable and one POST — no provider account, no
sender domain, no deliverability debugging — and it reaches a phone, which is
where you are on a Monday morning. Email is deliberately deferred *behind this
same seam*, so adding it later is one module rather than a rework (the adapter
pin used for MCP and MLflow).

Unset `WATCH_WEBHOOK_URL` ⇒ notifications are a silent no-op, so nothing in the
worker has to branch on whether notifications are configured.
"""

from __future__ import annotations

import json
import os
import urllib.request

_TIMEOUT = 10


def is_configured() -> bool:
    return bool(os.getenv("WATCH_WEBHOOK_URL"))


def notify(text: str, link: str | None = None) -> bool:
    """Send one short notification. Returns whether it was delivered.

    Never raises: a dead webhook must not fail a Refresh that already succeeded
    and is already saved.
    """
    url = os.getenv("WATCH_WEBHOOK_URL")
    if not url:
        return False
    body = f"{text}\n{link}" if link else text
    # Discord expects `content`, Slack expects `text`. Sending both keys makes
    # one implementation work with either, and neither minds the extra field.
    payload = json.dumps({"content": body, "text": body}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "multi-tool-research-agent"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False
