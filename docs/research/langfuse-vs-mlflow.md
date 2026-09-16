# Langfuse vs MLflow for the Eval Suite

**Ticket:** R1 (#30), Map 2 (#23) — research only, no application code changed.
**Date:** 2026-09-16. **Scope:** pick the eval/observability tooling that E4 (the
tooling/harness/CI shape ticket) will build on.

## Why this document exists

Map 2 charts an **eval suite** for this LangGraph financial-research agent. It has to:

- score a 100–200 case set on **field-level extraction accuracy**, a **false-clear
  (missed-escalation) rate**, and **citation correctness**;
- run a **before/after comparison** — single-agent baseline vs a multi-agent version;
- run as a **CI eval-gate that executes fully offline** — agents talk to an MCP server
  over stdio, the vector store falls back to embedded Chroma, and there are **zero
  external services in CI**.

The question is which tool records traces, scores, and experiment comparisons for that
suite: **Langfuse** or **MLflow**, as of 2026.

Fixed constraints that drive the call: **Python 3.12**, **LangGraph**, a standing
**legibility-first / minimal-complexity** principle (ADR 0001, "optimize for legibility
over production hardening"), and a **solo / portfolio** project. And the framing that
matters most here — this is the map's **lowest-stakes** decision. Both tools are
instrumentation over the *same* case data. The scoring logic (field accuracy, false-clear
rate, citation checks) is our own deterministic code either way; the tool is only the
**sink** that stores results and renders comparisons. Swapping later is a rewiring, not a
redo (see "Swap-cost read" at the end). So the tie-breaker is the same one ADR 0001 sets:
**fewest moving parts a reviewer — and a CI job — has to hold.**

---

## Axis 1 — Tracing a multi-agent LangGraph run + attaching per-case scores

### Langfuse

Instrumenting LangGraph is the LangChain pattern: pass a `CallbackHandler` to the graph
invocation and Langfuse records one **trace** whose **spans** mirror each node, tool call,
and LLM call; a multi-agent run (supervisor + sub-agents, nested graphs) collapses into a
single trace tree.[^lf-langgraph][^lf-langchain] Since July 2026 the trace has an **agent
graph view** with two modes — *Aggregated* (overall shape, loops drawn as cycles) and
*Expanded* (every call as its own node) — which is genuinely nice for eyeballing handoffs
in a multi-agent run.[^lf-agentgraphs] **Scores** attach to a trace/observation via the
SDK or API (`create_score` / the score ingestion endpoint), keyed by name with numeric,
categorical, or boolean values — so per-case field-accuracy, an escalation flag, and a
citation-correctness score all land on the case's trace.[^lf-scores] This is Langfuse's
core strength: it is a purpose-built LLM/agent **observability** product, and the trace UI
is more polished for agent debugging than MLflow's.

### MLflow

`mlflow.langchain.autolog()` turns on automatic tracing for LangGraph (it is implemented
as an extension of the LangChain integration); every `invoke()` produces a **trace with
nested spans** for each node, tool call, and LLM interaction, logged to the active MLflow
experiment.[^mf-langgraph][^mf-lcautolog] Scoring is done through **scorers** — the core
eval primitive — run via `mlflow.genai.evaluate()`; MLflow ships built-in scorers and lets
you write **custom code-based scorers** that return primitive pass/fail or numeric values,
which is exactly the shape of our field-accuracy / false-clear / citation checks (no
LLM-judge required).[^mf-scorers][^mf-customscorers][^mf-eval] Scores/metrics are recorded
on the run and on the traces.

**Read:** both trace a multi-agent LangGraph run at span granularity and both attach
per-case scores. Langfuse's **trace/agent-graph UI is the nicer artifact to look at**;
MLflow's **scorer + `genai.evaluate()` model maps more directly onto "run a scored suite
and get metrics back."** For *this* suite, where the scores are deterministic code we
already have to write, the difference is aesthetic, not capability.

---

## Axis 2 — Regression / CI eval-gate

Both vendors ship a first-party pytest CI story, and both fail the build the same way — a
failing `assert` fails the pytest job, which fails the check, which blocks the PR.

- **MLflow:** mark a test `@mlflow.test`, run scorers with `mlflow.genai.evaluate()`, and
  assert on the result (`result.passed` is true iff all scorers pass all rows; `result.reason`
  explains failures). Every test in the session is recorded under one MLflow run, so the
  green check and the browsable record come from the same source. MLflow's guidance is to
  gate on **threshold deltas** (e.g. block on a 3–5% accuracy drop) rather than a bare
  pass/fail, and it documents GitHub Actions integration.[^mf-regression]
- **Langfuse:** `run_experiment(...)` runs inside a pytest test; you read a run-level score
  from `result.run_evaluations` and assert it against a threshold (or raise a
  `RegressionError`), and there is a `langfuse/experiment-action` GitHub Action.[^lf-cicd][^lf-testing]

**Read:** a tie on capability. The catch is *where the run executes* — Axis 4.

---

## Axis 3 — Experiment tracking for before/after (baseline vs multi-agent)

- **MLflow:** experiment tracking *is* the product's origin. Baseline and multi-agent are
  two **runs** under one experiment; params/metrics/traces log per run and the UI compares
  runs side by side. This is the native "before/after two configurations" case.[^mf-tracking]
- **Langfuse:** the equivalent is **Datasets + Experiments** — run the suite as two dataset
  runs and use the experiment comparison view; comparison is at the dataset-run level rather
  than MLflow's general run-comparison.[^lf-experiments][^lf-datasets]

**Read:** both do it; MLflow's run-comparison is a slightly more natural fit for "two
configurations, same cases, compare the metric columns."

---

## Axis 4 — Self-hostable / free-tier fit, Python, and **offline CI** — the deciding axis

This is where the two genuinely differ, and it is the constraint the map pins as
load-bearing: **zero external services in CI.**

### Langfuse needs a running server

Langfuse is a **server-based** product. Basic tracing, scores, datasets, and experiments
all flow to a Langfuse instance — Cloud or self-hosted. The CI/CD experiment flow
**explicitly requires** a reachable instance: the GitHub Action takes `langfuse_public_key`,
`langfuse_secret_key`, and `langfuse_base_url`, "initializes the Langfuse SDK client from
the action inputs," and connects to the server to load the dataset and persist results — it
**cannot run offline.**[^lf-cicd] Self-hosting is free and MIT-licensed, and Cloud has a
free Hobby tier (50k units/month, 2 users),[^lf-oss][^lf-pricing] but "self-host" here is
not lightweight: the v3+ stack is **Postgres + ClickHouse (OLAP for traces/scores) +
Redis/Valkey + S3/blob storage**, run as a multi-container Docker Compose stack.[^lf-selfhost][^lf-clickhouse]
For our CI gate that means either standing up that stack in CI (the opposite of
"zero external services") or reaching out to Langfuse Cloud (a network dependency + secrets
in CI, and no longer offline).

### MLflow runs fully local, no server

MLflow's client logs to a **local backend with no server process**. As of **MLflow 3.7.0**
the default backend is a local **SQLite** file (`sqlite:///mlflow.db`); prior versions
defaulted to a `./mlruns` file store — either way it is a file on disk, no daemon, no
external service.[^mf-tracking][^mf-backend][^mf-localdb] `MLFLOW_TRACKING_URI` selects the
backend; unset (or a local path / `sqlite:///…`) means "log here, locally," and the docs are
explicit that the tracking **server is optional** — needed only for shared/remote
storage.[^mf-tracking][^mf-serverdoc] Runs, traces, scores, and experiments all persist to
that local file and remain searchable; `mlflow ui` renders them locally afterward for human
review (a local dev convenience, not a CI dependency). MLflow is 100% open source under
**Apache 2.0**, no gated tiers on tracing/eval/tracking.[^mf-license] Our CI scorers are
deterministic code (no LLM judge), so `mlflow.genai.evaluate()` runs with **no network at
all**.

**Read:** for a zero-dependency offline CI gate, this axis is decisive. MLflow satisfies it
out of the box (write to a local SQLite/`mlruns` path in the CI workspace, upload as a build
artifact if desired). Langfuse requires a running server — a dependency the map explicitly
wants CI not to have.

---

## Recommended default → **MLflow**

For *this* repo, MLflow is the default. The reasoning is entirely Axis 4, with Axes 2–3 as
supporting fit:

1. **It runs the CI eval-gate with zero external services.** Log to a local
   `sqlite:///mlflow.db` (or `./mlruns`) in the CI workspace; no server, no DB container, no
   secrets, no network — which is precisely the "fully offline, zero external services in CI"
   constraint the map sets. Langfuse's own CI flow cannot run offline; it needs a reachable
   instance and keys.[^lf-cicd][^mf-tracking][^mf-backend]
2. **Its eval + regression model is a direct fit.** `mlflow.genai.evaluate()` + custom
   code-based scorers + `@mlflow.test` is the "run a scored suite in pytest and fail on
   regression" shape we need, with threshold-delta gating documented.[^mf-regression][^mf-customscorers]
3. **Before/after is native run-comparison.** Baseline vs multi-agent are two runs in one
   experiment, compared in-tool.[^mf-tracking]
4. **Fewest moving parts (ADR 0001).** One `pip install mlflow`, one env var, a local file.
   No Postgres+ClickHouse+Redis+S3 stack, no Cloud account, nothing for a reviewer to
   provision to reproduce the eval.[^lf-selfhost][^lf-clickhouse]

**Where Langfuse would win — and it's a real pull, just not here.** If the priority were a
**polished, hosted agent-observability UI** (the aggregated/expanded agent-graph view is
excellent for debugging live multi-agent traces),[^lf-agentgraphs] or if the agent were a
deployed service needing production tracing dashboards, Langfuse Cloud's free Hobby tier is
a strong, low-effort choice.[^lf-pricing] For an **offline CI gate on a portfolio project**,
that hosted UI is out of scope and its server requirement is a net cost. (MLflow tracing can
also be pushed to a server later if a hosted view is ever wanted — same local API, different
`MLFLOW_TRACKING_URI`.)

---

## Swap-cost read — thin, by construction

This decision is cheap to reverse **if E4 keeps the scoring logic tool-agnostic**, and it
should:

- The eval **harness** owns the loop and the truth: run each case through the agent, compute
  per-case results (extracted fields vs expected, escalation-flag vs expected, citation
  matches) and aggregate metrics (field accuracy, false-clear rate, citation correctness).
  This is plain Python over our own case data — **no tool in it.**
- The tool is only the **reporting sink**: "take these per-case + aggregate numbers and
  record/compare them." For MLflow that's `mlflow.genai.evaluate(...)` / `log_metric` into a
  local run; for Langfuse it's `run_experiment(...)` uploading to a server.

Put the sink behind a **one-module adapter** (e.g. `record_case(...)` / `record_run(...)`
/ `assert_thresholds(...)`) and swapping MLflow → Langfuse (or adding Langfuse for a hosted
view alongside a local MLflow gate) is rewiring that one module — **not** touching the
harness, the scorers, or the case set. Given that, don't over-invest in the choice: take
MLflow for the offline gate now, keep the adapter seam clean, revisit only if a hosted
observability UI becomes a real requirement.

---

## What E4 (tooling/harness/CI shape) can rely on

- **The CI gate can be fully offline with MLflow.** Set `MLFLOW_TRACKING_URI` to a local
  path / `sqlite:///mlflow.db` in the CI workspace; **no server, DB container, network, or
  secrets** required. Our scorers are deterministic code, so `mlflow.genai.evaluate()` makes
  **no external calls**.[^mf-tracking][^mf-backend][^mf-customscorers]
- **Fail-on-regression is a pytest assert.** `@mlflow.test` + `mlflow.genai.evaluate()` +
  assert on `result.passed` / threshold deltas; a failing assert blocks the PR. Gate on a
  delta (e.g. accuracy drop > 3–5%), not bare pass/fail.[^mf-regression]
- **Before/after = two runs in one experiment**, compared in-tool; no extra infra.[^mf-tracking]
- **LangGraph tracing is one line** (`mlflow.langchain.autolog()`), spans per node/tool/LLM,
  logged to the same local backend.[^mf-langgraph][^mf-lcautolog]
- **Keep the sink behind an adapter** so the tool stays swappable (see swap-cost read).
- **If Langfuse is ever chosen instead:** budget for a reachable Langfuse instance in/around
  CI (Cloud Hobby free tier, or a self-hosted Postgres+ClickHouse+Redis+S3 stack) **plus
  `LANGFUSE_*` secrets** — it is not an offline, zero-dependency option.[^lf-cicd][^lf-selfhost][^lf-clickhouse]

---

## Sources

[^lf-langgraph]: Langfuse — *Open Source Observability for LangGraph* (cookbook): pass the
    `CallbackHandler` to the graph; one trace with spans per step; multi-agent runs as one
    trace tree. https://langfuse.com/guides/cookbook/integration_langgraph
[^lf-langchain]: Langfuse — *LangChain Tracing & Callbacks* (LangChain/LangGraph integration).
    https://langfuse.com/integrations/frameworks/langchain
[^lf-agentgraphs]: Langfuse — *Agent Graphs*: Aggregated vs Expanded graph-view modes (since
    July 2026). https://langfuse.com/docs/observability/features/agent-graphs
[^lf-scores]: Langfuse — *Scores via API/SDK*: attach numeric/categorical/boolean scores to
    traces/observations. https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk
[^lf-cicd]: Langfuse — *Experiments in CI/CD*: GitHub Action requires `langfuse_public_key` /
    `langfuse_secret_key` / `langfuse_base_url`; initializes the SDK client and connects to a
    Langfuse instance to load datasets and persist results (cannot run offline).
    https://langfuse.com/docs/evaluation/experiments/experiments-ci-cd
[^lf-testing]: Langfuse — *LLM Testing / Automated Tests for LLM Apps*: `run_experiment` inside
    pytest, read `result.run_evaluations`, assert vs threshold; raise `RegressionError`.
    https://langfuse.com/blog/2025-10-21-testing-llm-applications
[^lf-experiments]: Langfuse — *Experiments via SDK*.
    https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk
[^lf-datasets]: Langfuse — *Datasets*.
    https://langfuse.com/docs/evaluation/experiments/datasets
[^lf-selfhost]: Langfuse — *Self-host Langfuse*: Docker Compose runs web + worker + ClickHouse +
    Postgres + Redis + blob storage. https://langfuse.com/self-hosting
[^lf-clickhouse]: Langfuse — *ClickHouse (self-hosted)*: ClickHouse is the OLAP store for traces,
    observations, and scores. https://langfuse.com/self-hosting/deployment/infrastructure/clickhouse
[^lf-oss]: Langfuse — *Open-Source Strategy*: core is MIT-licensed; all product features (tracing,
    evals, prompts, experiments) with no usage limits in OSS. https://langfuse.com/docs/open-source
[^lf-pricing]: Langfuse — *Pricing*: Cloud Hobby free (50k units/month, 2 users); self-host free.
    https://langfuse.com/pricing
[^mf-langgraph]: MLflow — *Tracing LangGraph*: `mlflow.langchain.autolog()` auto-captures each
    node/tool/LLM as nested spans; only traces are logged for LangGraph.
    https://mlflow.org/docs/latest/genai/tracing/integrations/listing/langgraph
[^mf-lcautolog]: MLflow — *LangChain Autologging*: enable/disable auto-tracing.
    https://mlflow.org/docs/latest/genai/flavors/langchain/autologging/
[^mf-eval]: MLflow — *LLM and Agent Evaluation* overview.
    https://mlflow.org/docs/latest/genai/eval-monitor/
[^mf-scorers]: MLflow — *LLM Judges and Scorers*: scorers used with `mlflow.genai.evaluate()`;
    built-in + custom. https://mlflow.org/docs/latest/genai/eval-monitor/scorers/
[^mf-customscorers]: MLflow — *Create custom code-based scorers*: code scorers return
    pass/fail or numeric values; work with `mlflow.genai.evaluate()` for offline evaluation.
    https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/
[^mf-regression]: MLflow — *Regression Testing and CI/CD*: `@mlflow.test` + `mlflow.genai.evaluate()`;
    `result.passed` / `result.reason`; assert-fails-the-PR; threshold-delta gating; GitHub Actions.
    https://mlflow.org/docs/latest/genai/eval-monitor/regression-testing/
[^mf-tracking]: MLflow — *Experiment Tracking*: defaults to local logging with no server;
    `MLFLOW_TRACKING_URI` may be a local path, SQLite, or a remote server; server is optional.
    https://mlflow.org/docs/latest/ml/tracking/
[^mf-backend]: MLflow — *Backend Stores*: SQLite default (`sqlite:///mlflow.db`) as of MLflow
    3.7.0 (previously `./mlruns` file store); no separate DB server needed for local use.
    https://mlflow.org/docs/latest/ml/tracking/backend-stores/
[^mf-localdb]: MLflow — *Tracking Experiments with a Local Database*: SQLite on the local
    filesystem, no server. https://mlflow.org/docs/latest/ml/tracking/tutorials/local-database/
[^mf-serverdoc]: MLflow — *MLflow Tracking Server*: server enables remote/team storage; optional
    for local development. https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/
[^mf-license]: MLflow — homepage: 100% open source under Apache 2.0; core tracing/eval/tracking
    with no enterprise paywall. https://mlflow.org/
