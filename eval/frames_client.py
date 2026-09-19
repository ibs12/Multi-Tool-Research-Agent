"""
eval/frames_client.py
─────────────────────
A thin client over the SEC XBRL structured-financials API — the machine-
generated, accession-pinned ground-truth source chosen in R2 (Map 2).

Two endpoints:
  frames(concept, uom, cy)  — every filer's value for one concept in one period
                              (bulk discovery; each point carries its accession)
  companyfacts(cik)         — one company's full fact history, per concept a list
                              of {end, val, accn, fy, fp, form, frame} entries

`companyfacts` is the same XBRL API family as `frames` and the same accession-
pinned ground truth, but per-company — which is what lets the generator build
multi-concept cases from ONE accession and detect the E6.Q2 structural signals
(restatements = one period with several accessions/values; amendments =
10-K/A vs 10-K figure changes) without cross-joining many frame calls.

SEC etiquette (R2): a descriptive User-Agent is required, and the rate limit is
10 req/s. We sleep between live calls and cache every response on disk, so a
re-run is reproducible, fast, and kind to SEC.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

# A real contact per SEC policy; overridable so a fork sets its own.
# SEC is picky about this string — keep the proven "Name Team email" form.
USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "Multi-Tool-Research-Agent eval ibrahimallahb@gmail.com",
)
_MIN_INTERVAL = 0.15          # ≈6-7 req/s, comfortably under SEC's 10/s
_CACHE_DIR = Path(__file__).resolve().parent / ".cache"

_last_request = 0.0


def _throttle() -> None:
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


def _cache_path(url: str) -> Path:
    return _CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")


def get_json(url: str, use_cache: bool = True) -> dict:
    """Fetch + decode a SEC JSON endpoint, caching the response on disk."""
    cache = _cache_path(url)
    if use_cache and cache.exists():
        return json.loads(cache.read_text())

    _throttle()
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    data = json.loads(raw)

    if use_cache:
        _CACHE_DIR.mkdir(exist_ok=True)
        cache.write_text(json.dumps(data))
    return data


# ── Endpoints ─────────────────────────────────────────────────────────────────

def frames(concept: str, uom: str, cy: str, taxonomy: str = "us-gaap") -> list[dict]:
    """Every filer's value for `concept` in calendar frame `cy` (e.g. 'CY2023').

    `uom` is the frames unit path segment: 'USD' for money, 'USD-per-shares' for
    EPS. Returns the `data` list; each point is {accn, cik, entityName, start,
    end, val}.
    """
    url = f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{concept}/{uom}/{cy}.json"
    return get_json(url).get("data", [])


def companyfacts(cik: int | str) -> dict:
    """One company's full XBRL fact set. `cik` is zero-padded to 10 digits."""
    cik10 = str(int(cik)).zfill(10)
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    return get_json(url)


def concept_entries(facts: dict, concept: str, uom: str = "USD",
                    taxonomy: str = "us-gaap") -> list[dict]:
    """All entries for one concept from a companyfacts payload, or [] if absent.

    Each entry: {start?, end, val, accn, fy, fp, form, frame}. Missing concept
    (KeyError) returns [] — that absence is itself a signal (a missing GrossProfit
    tag → gross margin undisclosed, E6.Q2).
    """
    try:
        return facts["facts"][taxonomy][concept]["units"][uom]
    except (KeyError, TypeError):
        return []
