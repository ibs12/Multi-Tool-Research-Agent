# Before/after — single vs multi (7 cases, 2 should-escalate)
_dataset: e6-2026-09-19_

## 1. Capability (contextual, not a delta)
The single-agent baseline has no escalation mechanism (0 escalations); the multi-agent pipeline escalated 6. This is an added capability, not a percentage-point improvement.

## 2. Quality — multi-agent, absolute (headline)
- boundary false-clear: **None**
- overall false-clear: 0.0  |  false-escalate (guardrail): **0.8**

## 3. Genuine deltas (equal-capability metrics)
- field accuracy: single 0.0 → multi 0.0
- citation correctness: single None → multi None
- fabrications: single 0 → multi 0

## Decision (pre-registered): **null (insufficient data on this subset)**
checks: {"boundary_false_clear": null, "false_escalate": false, "no_field_regression": true}

_latency: single 542.7s, multi 2713.2s_