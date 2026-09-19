# RUNBOOK — the full before/after headline run (E5)

The single-vs-multi headline comparison over the **live eval set** (134 cases:
the frozen dataset's 124 correct/ambiguous/missing cases + 10 live escalation
queries). Each multi-agent case is a full research → risk → compliance pipeline
against live web/SEC, so it takes **minutes** — budget:

| Arm | Per case | 134 cases |
|---|---|---|
| single | ~1–2 min | ~2–4 h |
| multi | ~5–7 min | **~12–16 h** |

This is an **out-of-band batch job** — a server, a workstation left running, or
nightly CI. Not interactive. It is resumable, so kill and restart freely.

## Prerequisites

```bash
source myenv/bin/activate
# .env must hold ANTHROPIC_API_KEY + TAVILY_API_KEY
export PGVECTOR_URL=            # empty → embedded Chroma, zero external deps
export CLAUDE_MODEL=claude-opus-4-8
```

## Run it (survives a disconnect)

```bash
# one arm at a time is easiest to babysit; single first (fast), then multi
nohup python eval/run_full.py --arm single  > eval/runs/single.log 2>&1 &
nohup python eval/run_full.py --arm multi   > eval/runs/multi.log  2>&1 &
# ...or both sequentially in one process:
nohup python eval/run_full.py --arm both    > eval/runs/full.log   2>&1 &
```

`screen`/`tmux` work equally well. Watch progress:

```bash
tail -f eval/runs/multi.log          # one line per case: [multi] 42/134 ce-... -> brief
```

## Resume after an interruption

Every finished case is appended to `eval/runs/<arm>.checkpoint.jsonl` the instant
it completes. Just **re-run the same command** — it skips everything already done:

```bash
python eval/run_full.py --arm multi   # "[multi] 42 done, 92 to run"
```

## Get the numbers

Once **both** arms have every case checkpointed, score + compare (also runs
automatically at the end of an `--arm both` run):

```bash
python eval/run_full.py --report-only
```

Outputs:
- `eval/full_comparison_report.md` / `.json` — the capability/quality split
- MLflow (both arms, all metrics):
  ```bash
  mlflow ui --backend-store-uri sqlite:///eval/mlflow.db      # http://localhost:5000
  ```

## Reading the result (E5.Q2)

- **Capability** (contextual, not a delta): single-agent escalations should be
  **0** (no mechanism); multi > 0. State as an added capability.
- **Quality** (headline): the multi arm's **absolute** boundary false-clear +
  false-escalate — never false-clear alone (E3).
- **Deltas** (equal-capability): field accuracy / citation / fabrication, single
  vs multi.
- The pre-registered decision rule (`eval/compare.py:DECISION_RULE`) prints
  pass / mixed / null — report it honestly, including a null or mixed result.

## Cost note

~134 multi runs × several LLM calls each + ~134 single runs = a few thousand
Claude calls plus live web/SEC traffic. Estimate against your token pricing
before launching; consider `--limit N` for a smaller confirmatory run first.
