"""
eval/tracking.py
────────────────
The **one** module that touches the experiment tracker (E4.Q2). Everything
tracker-specific lives here, so swapping MLflow → Langfuse later is a one-file
change — the same adapter-pin discipline as the MCP client (ADR-0010).

Public surface (the names E4 fixed):
    record_case(arm, case_id, result)   — one case's per-case result
    record_run(arm, metrics, params)    — one arm's aggregate run
    assert_thresholds(metrics, rules)   — the Tier-1 gate (PURE, no tracker)

R1 chose MLflow with a local file store — no server, offline-friendly. The
import is lazy and optional: with mlflow absent, record_* fall back to local
JSON/JSONL so dev and the CI gate still capture results. `assert_thresholds`
never needs mlflow, which is exactly why the merge-blocking gate stays cheap
and dependency-free (E4.Q3).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# SQLite local store (R1) — the file store is deprecated in MLflow 3.x; SQLite
# is the supported offline backend and needs no running server.
_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{_HERE / 'mlflow.db'}")
_EXPERIMENT = os.getenv("MLFLOW_EXPERIMENT", "financial-agent-eval")
_RUNS_DIR = _HERE / "runs"


def _mlflow():
    try:
        import mlflow
        return mlflow
    except Exception:
        return None


# ── Tier-1 gate: pure threshold assertions ────────────────────────────────────

class ThresholdError(AssertionError):
    """A tracked metric violated its gate bound."""


_OPS = {"<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b,
        "<": lambda a, b: a < b, ">": lambda a, b: a > b, "==": lambda a, b: a == b}


def assert_thresholds(metrics: dict, rules: dict) -> None:
    """Raise ThresholdError if any metric violates its rule.

    rules: {metric_name: (op, bound)} with op in <=, >=, <, >, ==. A metric that
    is None (no denominator, e.g. no should-escalate cases ran) is a violation —
    the gate must not silently pass on a missing number.
    """
    violations = []
    for metric, (op, bound) in rules.items():
        val = metrics.get(metric)
        if val is None:
            violations.append(f"{metric}: no value to check against {op} {bound}")
        elif not _OPS[op](val, bound):
            violations.append(f"{metric}={val} violates {op} {bound}")
    if violations:
        raise ThresholdError("; ".join(violations))


# ── Recording (Tier-2 / experiment tracking) ──────────────────────────────────

def record_case(arm: str, case_id: str, result: dict) -> None:
    """Append one case's per-case result under this arm (E4.Q2)."""
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with (_RUNS_DIR / f"{arm}.cases.jsonl").open("a") as f:
        f.write(json.dumps({"case_id": case_id, **result}) + "\n")


def record_run(arm: str, metrics: dict, params: dict | None = None) -> dict:
    """Log one arm's aggregate run. Params pin the comparison inputs
    (dataset/model/prompt version) so both E5 arms are apples-to-apples."""
    params = {"arm": arm, **(params or {})}
    mlflow = _mlflow()
    if mlflow is None:
        return _record_local(arm, metrics, params)
    try:
        mlflow.set_tracking_uri(_TRACKING_URI)
        mlflow.set_experiment(_EXPERIMENT)
        with mlflow.start_run(run_name=arm):
            mlflow.log_params(params)
            for key, val in _flatten(metrics).items():
                mlflow.log_metric(key, val)
            try:
                mlflow.log_dict(metrics, "metrics.json")   # full copy incl. non-numeric
            except Exception:
                pass                                        # artifact store optional
        return {"backend": "mlflow", "tracking_uri": _TRACKING_URI, "arm": arm}
    except Exception as e:
        # Tracker store unavailable (e.g. skinny install without the DB deps) —
        # never lose the numbers; fall back to local JSON. The metrics are the
        # asset; the backend is swappable (E4).
        local = _record_local(arm, metrics, params)
        local["mlflow_error"] = str(e)
        return local


def _record_local(arm: str, metrics: dict, params: dict) -> dict:
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = _RUNS_DIR / f"{arm}.run.json"
    path.write_text(json.dumps({"params": params, "metrics": metrics}, indent=2))
    return {"backend": "local", "path": str(path), "arm": arm}


def _flatten(d: dict, prefix: str = "") -> dict:
    """Flatten nested numeric metrics to dotted keys mlflow.log_metric accepts."""
    flat = {}
    for key, val in d.items():
        name = f"{prefix}{key}"
        if isinstance(val, dict):
            flat.update(_flatten(val, name + "."))
        elif isinstance(val, bool):
            continue
        elif isinstance(val, (int, float)):
            flat[name] = val
    return flat
