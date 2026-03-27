# AIP-XX: AI Pipeline Governance Provider

**Author:** [Your Name]  
**Status:** Draft  
**Created:** 2026-03-26  
**Category:** Provider  
**Discussions-To:** https://github.com/apache/airflow/discussions

---

## Abstract

This AIP proposes `apache-airflow-provider-ai-governance` — a first-class provider for governing AI/LLM workloads orchestrated by Apache Airflow. It does for AI pipeline quality what Great Expectations did for data quality: replaces every team's ad-hoc evaluation scripts with a standardized, composable, observable governance layer built directly into the DAG.

---

## Motivation

AI pipelines in production — RAG pipelines, prompt chains, model retraining jobs, LLM-based ETL — are now routinely orchestrated with Airflow. Yet there is no standard way to:

- Assert that an LLM's output meets quality thresholds before passing it downstream
- Track which prompt version produced which output
- Enforce token budget limits across a DAG run
- Detect when a model's output distribution has drifted
- Gate a deployment on hallucination rate

Every team solves this with custom scripts. This is identical to the state of data quality before Great Expectations (2018). The orchestrator is the right place to standardize governance — it sees every pipeline run, controls execution flow, and already owns retry/failure logic.

### The analogy

| | Great Expectations (2018) | This provider (2026) |
|---|---|---|
| Problem | Is the data correct? | Is the AI output trustworthy? |
| Status quo | Custom `assert` scripts per team | Custom `eval_outputs.py` per team |
| What it provides | Expectations, checkpoints, data docs | Quality gates, prompt registry, eval results |
| Enforcement point | Task boundary | Task boundary |
| Result | Industry standard | To be established |

---

## Goals

1. Provide Operators that enforce AI quality gates as first-class DAG tasks
2. Provide Sensors that wait on model or eval conditions before proceeding
3. Provide Hooks that integrate with the major AI observability platforms
4. Provide a Governance Store (backed by Airflow's existing metadata DB) for prompt versions, eval results, and token ledgers
5. Extend the Airflow UI with an AI Quality dashboard
6. Expose `airflow ai-gov` CLI commands for local development and CI

## Non-Goals

- Training or serving models (Airflow is the orchestrator, not the platform)
- Replacing LangSmith, W&B, or Arize (we integrate with them, not replace them)
- Supporting non-LLM ML workloads in v1 (future AIP)

---

## Proposed Design

### Package structure

```
apache-airflow-provider-ai-governance/
├── airflow/
│   └── providers/
│       └── ai_governance/
│           ├── __init__.py
│           ├── operators/
│           │   ├── llm_quality_check.py
│           │   ├── prompt_version.py
│           │   ├── token_budget_guard.py
│           │   ├── model_drift.py
│           │   └── hallucination_gate.py
│           ├── sensors/
│           │   ├── model_readiness.py
│           │   ├── eval_threshold.py
│           │   └── data_drift.py
│           ├── hooks/
│           │   ├── openai_governance.py
│           │   ├── langsmith.py
│           │   ├── weights_and_biases.py
│           │   └── arize.py
│           ├── store/
│           │   ├── prompt_registry.py
│           │   ├── eval_results.py
│           │   └── token_ledger.py
│           ├── models/
│           │   └── governance_result.py
│           └── ui/
│               └── ai_quality_plugin.py
├── tests/
└── provider.yaml
```

---

## Operators

### 1. `LLMQualityCheckOperator`

Runs a set of evaluation functions against LLM output and fails the task (or warns) if thresholds are not met. Analogous to a Great Expectations checkpoint.

```python
from airflow.providers.ai_governance.operators.llm_quality_check import LLMQualityCheckOperator

check = LLMQualityCheckOperator(
    task_id="check_output_quality",
    input_xcom_task_id="call_llm",
    evaluators=[
        "relevance",          # built-in: cosine similarity to input
        "no_pii",             # built-in: regex + NER scan
        "toxicity < 0.05",    # built-in: threshold expression
        my_custom_evaluator,  # callable: (input, output) -> float
    ],
    fail_on=["no_pii", "toxicity"],   # hard failures
    warn_on=["relevance < 0.7"],      # soft warnings, logged but don't fail
    result_xcom_key="quality_result",
)
```

**Key design decisions:**
- `evaluators` accepts both built-in string shortcuts and arbitrary callables — same pattern as Great Expectations' `expect_*` API
- `fail_on` vs `warn_on` maps to GE's `critical` vs `warning` distinction
- Result is pushed to XCom as a `GovernanceResult` object for downstream inspection
- Built-in evaluators run locally; external evaluators (LangSmith, Arize) call the hook

**Built-in evaluators (v1):**

| Name | Description | Returns |
|---|---|---|
| `relevance` | Cosine similarity between input and output embeddings | float 0–1 |
| `toxicity` | Toxic content score via lightweight classifier | float 0–1 |
| `no_pii` | PII detection via regex + optional NER | bool |
| `completeness` | Output length relative to expected range | bool |
| `json_valid` | Output parses as valid JSON | bool |
| `json_schema` | Output matches provided JSON schema | bool |
| `no_hallucination` | Factual grounding check against source docs | float 0–1 |

---

### 2. `PromptVersionOperator`

Registers a prompt template with the Governance Store before use, creating an immutable versioned record linked to each DAG run.

```python
from airflow.providers.ai_governance.operators.prompt_version import PromptVersionOperator

register_prompt = PromptVersionOperator(
    task_id="register_prompt",
    prompt_id="summarize_orders_v2",
    template="Summarize the following orders for a finance audience: {orders}",
    model="gpt-4o",
    tags={"team": "finance", "use_case": "daily_summary"},
    push_version_to_xcom=True,   # downstream tasks receive prompt_version_id
)
```

**What gets stored:**
- SHA-256 hash of the template string
- Model identifier
- DAG run ID, task ID, execution date
- Tags and metadata
- Diff from previous version of same `prompt_id`

**Why it matters:** When an LLM output degrades two weeks from now, you can trace it to the exact prompt version that was active. This is the "git blame" for prompts.

---

### 3. `TokenBudgetGuardOperator`

Enforces a token spend budget at task, DAG, or team level. Fails or skips the task if the budget would be exceeded.

```python
from airflow.providers.ai_governance.operators.token_budget_guard import TokenBudgetGuardOperator

guard = TokenBudgetGuardOperator(
    task_id="check_token_budget",
    budget_id="finance_team_daily",
    estimated_tokens=4000,          # from upstream task's XCom
    hard_limit=100_000,             # fail if over
    soft_limit=80_000,              # warn if over
    period="daily",                 # rolling window
    on_exceed="fail",               # or: "skip", "warn"
)
```

**Token ledger:** Backed by a lightweight table in Airflow's metadata DB. Supports queries like "how many tokens did DAG X spend this week?" surfaced in the UI dashboard.

---

### 4. `ModelDriftOperator`

Compares the current model's output distribution against a baseline snapshot. Fails if drift exceeds threshold.

```python
from airflow.providers.ai_governance.operators.model_drift import ModelDriftOperator

drift_check = ModelDriftOperator(
    task_id="check_model_drift",
    model_id="gpt-4o",
    baseline_run_id="scheduled__2026-01-01",  # or "last_7_days"
    output_xcom_task_id="call_llm",
    metrics=["embedding_distance", "sentiment_shift", "length_distribution"],
    drift_threshold=0.15,
    hook_conn_id="arize_default",   # optional: forward results to Arize
)
```

---

### 5. `HallucinationGateOperator`

Scores LLM output against a set of source documents for factual grounding. Blocks the pipeline if hallucination rate exceeds threshold.

```python
from airflow.providers.ai_governance.operators.hallucination_gate import HallucinationGateOperator

gate = HallucinationGateOperator(
    task_id="hallucination_gate",
    output_xcom_task_id="generate_report",
    source_docs_xcom_task_id="fetch_source_docs",
    method="nli",                   # natural language inference; or "embeddings", "llm_judge"
    threshold=0.95,                 # minimum grounding score to pass
    fail_on_exceed=True,
)
```

---

## Sensors

### `ModelReadinessSensor`

Waits until a model endpoint passes a health + capability check before allowing downstream tasks to proceed.

```python
from airflow.providers.ai_governance.sensors.model_readiness import ModelReadinessSensor

wait_for_model = ModelReadinessSensor(
    task_id="wait_for_model",
    model_id="gpt-4o",
    hook_conn_id="openai_default",
    capability_checks=["function_calling", "json_mode"],
    timeout=600,
    poke_interval=30,
)
```

### `EvalThresholdSensor`

Waits until rolling eval metrics (from the Governance Store) rise above a threshold — useful for gating production traffic on canary model performance.

```python
from airflow.providers.ai_governance.sensors.eval_threshold import EvalThresholdSensor

wait_for_quality = EvalThresholdSensor(
    task_id="wait_for_quality_threshold",
    metric="relevance",
    model_id="ft-gpt4o-v3",
    threshold=0.85,
    window="last_100_runs",
    timeout=3600,
)
```

### `DataDriftSensor`

Waits until input data distribution stabilizes before triggering an LLM pipeline — avoids running expensive prompts on anomalous data.

---

## Hooks

Hooks handle the authenticated connection to external AI observability platforms. They follow Airflow's standard Hook pattern, registered via `get_conn()`.

### `LangSmithHook`

```python
from airflow.providers.ai_governance.hooks.langsmith import LangSmithHook

hook = LangSmithHook(conn_id="langsmith_default")
hook.log_run(
    run_name="summarize_orders",
    inputs={"orders": orders_data},
    outputs={"summary": llm_output},
    tags=["production", "finance"],
)
run_url = hook.get_run_url(run_id)
```

### `WeightsAndBiasesHook`

Logs eval metrics, prompt versions, and drift scores to W&B runs. Supports both online and offline (file-based) logging.

### `ArizeHook`

Forwards production inference records to Arize for continuous monitoring. Maps `GovernanceResult` fields to Arize's schema automatically.

### `OpenAIGovernanceHook`

Wraps the standard OpenAI hook with token counting, rate limit awareness, and automatic logging to the Governance Store on each call.

---

## Governance Store

A lightweight persistence layer backed by Airflow's existing metadata database (no new external dependency required).

### Schema additions

```sql
-- Prompt registry
CREATE TABLE ai_gov_prompt_versions (
    id            VARCHAR(64) PRIMARY KEY,  -- SHA-256 of template
    prompt_id     VARCHAR(255) NOT NULL,
    template_hash VARCHAR(64) NOT NULL,
    model         VARCHAR(255),
    tags          JSONB,
    dag_id        VARCHAR(250),
    run_id        VARCHAR(250),
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Eval results
CREATE TABLE ai_gov_eval_results (
    id              SERIAL PRIMARY KEY,
    dag_id          VARCHAR(250),
    run_id          VARCHAR(250),
    task_id         VARCHAR(250),
    prompt_version  VARCHAR(64) REFERENCES ai_gov_prompt_versions(id),
    evaluator       VARCHAR(255),
    score           FLOAT,
    passed          BOOLEAN,
    metadata        JSONB,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Token ledger
CREATE TABLE ai_gov_token_ledger (
    id          SERIAL PRIMARY KEY,
    budget_id   VARCHAR(255) NOT NULL,
    dag_id      VARCHAR(250),
    run_id      VARCHAR(250),
    task_id     VARCHAR(250),
    model       VARCHAR(255),
    tokens_used INTEGER NOT NULL,
    period_key  VARCHAR(32),   -- e.g. "2026-03-26" for daily
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## GovernanceResult — XCom model

All operators push a typed `GovernanceResult` to XCom for downstream use:

```python
@dataclass
class GovernanceResult:
    passed: bool
    score: float | None
    evaluator: str
    input_hash: str            # SHA-256 of the input, for traceability
    prompt_version_id: str | None
    token_count: int | None
    metadata: dict
    dag_id: str
    run_id: str
    task_id: str
    timestamp: datetime

    def to_dict(self) -> dict: ...
    def raise_if_failed(self) -> None: ...
```

Downstream tasks can branch on `GovernanceResult.passed` or inspect individual scores:

```python
from airflow.providers.ai_governance.models.governance_result import GovernanceResult

def branch_on_quality(**context):
    result = GovernanceResult.from_xcom(context, task_id="check_output_quality")
    if result.passed:
        return "send_to_warehouse"
    return "flag_for_human_review"
```

---

## UI Extension

An Airflow plugin adds an **AI Quality** tab to the navigation. It surfaces:

- Per-DAG eval score trends (line chart, last 30 runs)
- Token spend by DAG, team, and model (bar chart, configurable period)
- Prompt version history with diff view
- Failed gate log with input/output samples (redacted if PII flag set)
- Drift alerts with baseline vs current distribution charts

Built as a React plugin using Airflow's existing plugin mechanism — no new dependencies.

---

## CLI Extension

```bash
# Check eval scores for a DAG
airflow ai-gov status --dag-id my_llm_pipeline

# Show prompt version history
airflow ai-gov prompts --prompt-id summarize_orders --last 10

# Show token spend
airflow ai-gov budget --budget-id finance_team_daily --period week

# Run evals locally against a prompt + dataset
airflow ai-gov eval --prompt-id summarize_orders --dataset orders_sample.jsonl

# Diff two prompt versions
airflow ai-gov diff --prompt-id summarize_orders --from v3 --to v4
```

---

## Example DAG

A complete RAG pipeline with governance gates at every AI boundary:

```python
from datetime import datetime
from airflow.decorators import dag, task
from airflow.providers.ai_governance.operators.prompt_version import PromptVersionOperator
from airflow.providers.ai_governance.operators.token_budget_guard import TokenBudgetGuardOperator
from airflow.providers.ai_governance.operators.llm_quality_check import LLMQualityCheckOperator
from airflow.providers.ai_governance.operators.hallucination_gate import HallucinationGateOperator
from airflow.providers.ai_governance.sensors.model_readiness import ModelReadinessSensor

@dag(schedule="0 6 * * *", start_date=datetime(2026, 1, 1))
def governed_rag_pipeline():

    ready = ModelReadinessSensor(
        task_id="wait_for_model",
        model_id="gpt-4o",
        hook_conn_id="openai_default",
        timeout=300,
    )

    register_prompt = PromptVersionOperator(
        task_id="register_prompt",
        prompt_id="daily_orders_summary",
        template="Summarize the following orders concisely: {orders}",
        model="gpt-4o",
    )

    @task
    def fetch_source_docs():
        # ... retrieve from vector DB
        return docs

    @task
    def call_llm(prompt_version_id, source_docs):
        # ... call OpenAI with versioned prompt
        return {"output": llm_output, "tokens_used": usage.total_tokens}

    guard = TokenBudgetGuardOperator(
        task_id="token_budget_guard",
        budget_id="finance_team_daily",
        estimated_tokens="{{ ti.xcom_pull('call_llm')['tokens_used'] }}",
        hard_limit=500_000,
    )

    quality = LLMQualityCheckOperator(
        task_id="quality_check",
        input_xcom_task_id="call_llm",
        evaluators=["relevance", "no_pii", "toxicity < 0.05"],
        fail_on=["no_pii"],
        warn_on=["relevance < 0.7"],
    )

    hallucination = HallucinationGateOperator(
        task_id="hallucination_gate",
        output_xcom_task_id="call_llm",
        source_docs_xcom_task_id="fetch_source_docs",
        threshold=0.90,
    )

    @task
    def load_to_warehouse(quality_result, hallucination_result):
        quality_result.raise_if_failed()
        hallucination_result.raise_if_failed()
        # ... write to Snowflake

    docs = fetch_source_docs()
    llm_result = call_llm(register_prompt.output, docs)

    ready >> register_prompt >> llm_result
    llm_result >> guard >> quality >> hallucination >> load_to_warehouse(quality.output, hallucination.output)

governed_rag_pipeline()
```

---

## Connections

| Conn ID | Conn Type | Used By |
|---|---|---|
| `openai_default` | HTTP | `OpenAIGovernanceHook`, `ModelReadinessSensor` |
| `langsmith_default` | HTTP | `LangSmithHook` |
| `wandb_default` | HTTP | `WeightsAndBiasesHook` |
| `arize_default` | HTTP | `ArizeHook` |

All connections use Airflow's standard `BaseHook.get_connection()` — no new connection types required in v1.

---

## Backwards Compatibility

This is a new provider package. No changes to Airflow core. Fully additive.

The Governance Store schema additions are applied via Alembic migrations only when the provider is installed, following the precedent set by other providers that extend the metadata DB.

---

## Implementation Plan

### Phase 1 — Core operators + store (v1.0)
- `LLMQualityCheckOperator` with 7 built-in evaluators
- `PromptVersionOperator` + registry schema
- `TokenBudgetGuardOperator` + ledger schema
- `LangSmithHook`, `OpenAIGovernanceHook`
- `GovernanceResult` XCom model
- Unit tests + integration tests with mocked LLM responses

### Phase 2 — Sensors + drift (v1.1)
- `ModelReadinessSensor`, `EvalThresholdSensor`
- `ModelDriftOperator`
- `WeightsAndBiasesHook`, `ArizeHook`

### Phase 3 — UI + CLI (v1.2)
- AI Quality dashboard plugin
- `airflow ai-gov` CLI commands
- `HallucinationGateOperator`

### Phase 4 — Ecosystem (v2.0)
- OpenTelemetry export of all governance metrics
- Provider integration with `apache-airflow-provider-openai` (reuse connection types)
- Support for non-OpenAI models: Anthropic, Gemini, local Ollama

---

## Alternatives Considered

**External tools only (LangSmith, Arize, W&B):** These are excellent but live outside the DAG. A task failure from a quality gate needs to be modeled in Airflow's task graph — you can't branch, retry with different prompts, or trigger alerts through Airflow's existing mechanisms if the evaluation happens in an external tool.

**Custom PythonOperator approach (status quo):** Works but is not composable, not observable through Airflow's UI, not standardized across teams, and produces no audit trail in the metadata DB.

**A new core Airflow feature:** Governance concerns are provider-level, not core. Keeping this as a provider allows faster iteration and optional adoption.

---

## References

- Great Expectations provider: `apache-airflow-provider-great-expectations`
- Airflow Provider development guide: https://airflow.apache.org/docs/apache-airflow-providers/
- AIP-72 (Dataset / Data-Aware Scheduling) — related prior art on data boundaries
- LangSmith API docs: https://docs.smith.langchain.com/
- Arize AI: https://arize.com/
