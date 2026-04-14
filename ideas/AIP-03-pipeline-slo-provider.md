# AIP-ZZ: Pipeline SLO Provider — Service Level Objectives as First-Class Citizens

**Author:** [Your Name]
**Status:** Draft
**Created:** 2026-03-26
**Category:** Provider
**Discussions-To:** https://github.com/apache/airflow/discussions

---

## Abstract

This AIP proposes `apache-airflow-provider-pipeline-slo` — a provider that brings Service Level Objectives (SLOs), error budgets, and burn rate alerting natively into Apache Airflow DAGs. SREs have used SLOs to govern microservices since Google's SRE book (2016). Data pipelines have had nothing equivalent — teams track "did the DAG succeed?" but nobody tracks "are we consuming error budget faster than we can afford?" This provider closes that gap, making pipeline reliability a first-class, measurable, enforceable contract between data teams and the business.

---

## Motivation

### The status quo

Every data team informally knows their pipeline's reliability expectations. "The orders DAG must finish by 6am." "Finance can tolerate one failure per month." "The ML feature pipeline has a 99.5% uptime requirement." These agreements live in Confluence docs, Slack messages, and people's heads.

When things go wrong, the conversation is always reactive: "Why did the pipeline fail? How long was it down?" Never proactive: "We've consumed 40% of our monthly error budget in the first week — we need to freeze risky deployments."

SRE teams solved this for services a decade ago with SLOs + error budgets + burn rate alerting. Data pipeline teams are running the exact same playbook from 2010 — on-call rotations that respond to fires, with no quantitative measure of reliability health.

### What's missing

| SRE concept | Services today | Pipelines today | After this provider |
|---|---|---|---|
| SLI (indicator) | Request latency, error rate | DAG success rate, completion time | `SLOCheckpoint` measures it |
| SLO (objective) | 99.9% requests < 200ms | Finish by 6am, 99.5% of the time | YAML definition, stored in SLO Store |
| Error budget | 43.8 min/month downtime allowed | Nobody tracks this | Error Budget Ledger |
| Burn rate | Depleting budget 14x faster than normal | Nobody measures this | `BurnRateAlert` operator |
| Budget freeze | Halt risky changes when budget low | Never happens for pipelines | `ErrorBudgetGuard` blocks deploys |
| Compliance report | Monthly SLO review | Manual spreadsheet | `SLOReport` operator |

### The analogy that matters

Great Expectations asked: is the data correct?
The AI Governance provider asked: is the LLM output trustworthy?
This provider asks: **is the pipeline reliably meeting its business commitments?**

These are three different questions at three different layers of the data stack. This provider owns the reliability layer.

---

## Goals

1. Define SLOs for pipelines as code (YAML or Python), versioned alongside DAG definitions
2. Measure SLIs (latency, success rate, freshness) automatically at task and DAG boundaries
3. Maintain an error budget ledger per SLO in Airflow's metadata DB
4. Calculate and alert on burn rates — the speed at which budget is being consumed
5. Provide an `ErrorBudgetGuard` that blocks downstream tasks when budget is critically low
6. Integrate with PagerDuty, Datadog, Prometheus, OpsGenie, and Slack for alerting
7. Extend the Airflow UI with a live SLO dashboard
8. Expose `airflow slo` CLI commands for budget inspection and SLO management

## Non-Goals

- Replacing Datadog, Prometheus, or Grafana (we integrate with them, not replace them)
- Infrastructure-level SLOs (server uptime, DB availability) — those belong in existing SRE tooling
- Real-time sub-second SLO evaluation (Airflow's scheduler resolution is not suitable for this)

---

## Core Concepts

### SLI — Service Level Indicator

A measurable signal that reflects pipeline health. The provider supports three built-in SLI types:

**Latency SLI** — "What fraction of DAG runs complete within the target duration?"
```
latency_sli = successful_runs_under_target / total_runs
```

**Success Rate SLI** — "What fraction of DAG runs succeed?"
```
success_rate_sli = successful_runs / total_runs
```

**Freshness SLI** — "What fraction of time windows is the output data no older than the target?"
```
freshness_sli = windows_where_data_age < target / total_windows
```

### SLO — Service Level Objective

A target value for an SLI over a rolling window:
```
SLO: latency_sli >= 0.995 over 30 days
→ "95.5% of runs must complete within the target duration, measured over a rolling 30-day window"
```

### Error Budget

The amount of unreliability the SLO permits:
```
error_budget = (1 - SLO_target) × window_duration
→ 99.5% SLO over 30 days = 0.5% × 43,200 min = 216 minutes of allowed failure
```

### Burn Rate

How fast the error budget is being consumed relative to the allowed rate:
```
burn_rate = actual_error_rate / (1 - SLO_target)
→ burn_rate = 1.0 means consuming budget at exactly the right pace
→ burn_rate = 14.0 means consuming budget 14x too fast (alert!)
```

High burn rates are the key signal. A burn rate of 14x over 1 hour consumes 5.8% of a 30-day budget in a single hour — you will exhaust the budget in 2 days at that rate.

---

## SLO Definition — SLO-as-Code

SLOs are defined in YAML files, versioned alongside DAGs, and loaded by the provider on startup.

```yaml
# slos/orders_pipeline.yaml
slos:
  - id: orders_pipeline_latency
    dag_id: orders_pipeline
    description: "Orders pipeline must complete within 45 minutes, 99.5% of the time"
    sli_type: latency
    target: 0.995
    window_days: 30
    latency_target_minutes: 45
    alerting:
      burn_rate_windows:
        - window_hours: 1
          burn_rate_threshold: 14.4   # 2% budget in 1hr
          severity: critical
        - window_hours: 6
          burn_rate_threshold: 6.0    # 5% budget in 6hr
          severity: warning
      hooks:
        - pagerduty_default
        - slack_data_oncall
    budget_policy:
      freeze_deployments_below_percent: 10   # block ErrorBudgetGuard when <10% budget left
      notify_at_percent: [50, 25, 10, 5]

  - id: orders_pipeline_freshness
    dag_id: orders_pipeline
    description: "Orders data must not be older than 1 hour at the end of each run"
    sli_type: freshness
    target: 0.99
    window_days: 30
    freshness_target_minutes: 60
    output_task_id: load_to_warehouse
    freshness_xcom_key: data_as_of_timestamp
```

Alternatively, defined inline in Python using the SLO registry:

```python
from airflow.providers.pipeline_slo.models.slo import SLODefinition, SLIType

SLODefinition.register(
    id="orders_pipeline_latency",
    dag_id="orders_pipeline",
    sli_type=SLIType.LATENCY,
    target=0.995,
    window_days=30,
    latency_target_minutes=45,
    alerting={
        "burn_rate_windows": [
            {"window_hours": 1, "burn_rate_threshold": 14.4, "severity": "critical"},
            {"window_hours": 6, "burn_rate_threshold": 6.0, "severity": "warning"},
        ],
        "hooks": ["pagerduty_default"],
    },
)
```

---

## Proposed Design

### Package structure

```
apache-airflow-provider-pipeline-slo/
├── airflow/
│   └── providers/
│       └── pipeline_slo/
│           ├── __init__.py
│           ├── operators/
│           │   ├── slo_checkpoint.py
│           │   ├── error_budget_guard.py
│           │   ├── slo_annotate.py
│           │   ├── burn_rate_alert.py
│           │   └── slo_report.py
│           ├── sensors/
│           │   ├── slo_budget_remaining.py
│           │   ├── slo_burn_rate.py
│           │   └── slo_compliant.py
│           ├── hooks/
│           │   ├── pagerduty.py
│           │   ├── prometheus.py
│           │   ├── datadog.py
│           │   ├── opsgenie.py
│           │   └── slack_slo.py
│           ├── store/
│           │   ├── slo_definitions.py
│           │   ├── error_budget_ledger.py
│           │   └── burn_rate_log.py
│           ├── engine/
│           │   ├── sli_calculator.py     # core SLI computation
│           │   ├── budget_calculator.py  # error budget math
│           │   └── burn_rate_engine.py   # multi-window burn rate
│           ├── models/
│           │   ├── slo.py
│           │   └── slo_result.py
│           ├── listeners/
│           │   └── dag_run_listener.py   # auto-measures SLIs on every run
│           └── ui/
│               └── slo_dashboard_plugin.py
├── slo_schemas/
│   └── slo_definition.schema.json       # JSON schema for YAML validation
├── tests/
└── provider.yaml
```

---

## Operators

### 1. `SLOCheckpointOperator`

The anchor of the provider. Records an SLI measurement for a DAG run at the point where the operator runs. Most teams place it as the final task in their DAG — measuring end-to-end latency and updating the error budget.

```python
from airflow.providers.pipeline_slo.operators.slo_checkpoint import SLOCheckpointOperator

checkpoint = SLOCheckpointOperator(
    task_id="slo_checkpoint",
    slo_ids=["orders_pipeline_latency", "orders_pipeline_freshness"],
    # Optional: override freshness from XCom rather than wall clock
    freshness_xcom_task_id="load_to_warehouse",
    freshness_xcom_key="data_as_of_timestamp",
    # What to do when SLO is breached
    on_breach="warn",        # or: "fail", "callback"
    on_breach_callback=notify_data_lead,
)
```

**What happens on each run:**

1. Reads the SLO definition(s) from the SLO Store
2. Computes SLI values:
   - Latency SLI: `(execution_date → now) <= latency_target`
   - Success SLI: derived from DAG run state (no XCom needed)
   - Freshness SLI: `(now - data_as_of_timestamp) <= freshness_target`
3. Appends the measurement to the Error Budget Ledger
4. Recalculates remaining budget over the rolling window
5. Evaluates burn rates across all configured windows (1h, 6h, 24h, 72h)
6. Fires alerts via configured hooks if burn rate thresholds are exceeded
7. Pushes `SLOResult` to XCom for downstream inspection
8. Emits Prometheus/Datadog metrics if hooks are configured

**Auto-listener alternative:** For teams that cannot modify their DAGs, the provider also ships a DAG run listener that automatically records SLI measurements on every `dag_run.success` / `dag_run.failed` event — zero DAG changes required. The listener is registered via `provider.yaml`.

---

### 2. `ErrorBudgetGuardOperator`

Blocks a downstream task (typically a deployment, schema migration, or risky transformation) when the error budget is below a configured threshold. The data pipeline equivalent of "freeze risky changes when budget is low."

```python
from airflow.providers.pipeline_slo.operators.error_budget_guard import ErrorBudgetGuardOperator

guard = ErrorBudgetGuardOperator(
    task_id="budget_guard_before_migration",
    slo_id="orders_pipeline_latency",
    min_budget_percent=15,           # block if < 15% budget remaining
    on_insufficient="fail",          # or: "skip", "warn"
    override_var="SLO_BUDGET_OVERRIDE",  # Airflow Variable that bypasses guard (break-glass)
)
```

**Typical placement in DAG:**

```
fetch_data → transform → [budget_guard] → schema_migration → load → slo_checkpoint
```

The guard sits between risky operations and normal pipeline tasks. If the monthly budget has been largely consumed by earlier failures this month, the guard blocks the schema migration — protecting the remaining budget from being burned by a deployment that could wait.

**Break-glass mechanism:** Setting the Airflow Variable `SLO_BUDGET_OVERRIDE=true` bypasses the guard and logs the override with the operator's user context. Every bypass is audited.

---

### 3. `SLOAnnotateOperator`

Attaches a human-readable SLO annotation to a DAG run — used to mark planned maintenance windows, known incidents, or special conditions that should be excluded from SLI calculation.

```python
from airflow.providers.pipeline_slo.operators.slo_annotate import SLOAnnotateOperator

annotate = SLOAnnotateOperator(
    task_id="annotate_maintenance",
    slo_ids=["orders_pipeline_latency"],
    annotation_type="excluded",      # or: "degraded", "informational"
    reason="Planned Snowflake maintenance window — excluded from SLO calculation",
    approved_by="data-platform-oncall",
)
```

Excluded runs are removed from SLI calculation for the annotated SLOs — equivalent to an SRE declaring a maintenance window. Degraded annotations are included in SLI calculation but flagged in the dashboard for context.

---

### 4. `BurnRateAlertOperator`

Evaluates burn rates on demand across multiple time windows and fires alerts. Typically scheduled as a separate monitoring DAG that runs every 5 minutes, independent of the business DAG.

```python
from airflow.providers.pipeline_slo.operators.burn_rate_alert import BurnRateAlertOperator

alert = BurnRateAlertOperator(
    task_id="evaluate_burn_rates",
    slo_ids=["orders_pipeline_latency", "ml_feature_pipeline_freshness"],
    # Multi-window burn rate evaluation (Google SRE recommended pattern)
    windows=[
        {"hours": 1,  "threshold": 14.4, "severity": "critical"},  # 2% budget / 1hr
        {"hours": 6,  "threshold": 6.0,  "severity": "warning"},   # 5% budget / 6hr
        {"hours": 24, "threshold": 3.0,  "severity": "info"},      # 10% budget / 24hr
    ],
    alert_hooks=["pagerduty_default", "slack_data_oncall"],
    suppress_if_annotated=True,      # no alert during maintenance windows
)
```

**Multi-window alerting rationale (from Google SRE book):**

A single burn rate window creates problems:
- Short window (1h): catches fast burns but misses slow, sustained degradation
- Long window (72h): catches slow burns but reacts too slowly to fast incidents

Using multiple windows in combination avoids both failure modes:
- Critical alert: burn rate > 14.4x over 1h AND burn rate > 6x over 6h → fast, severe incident
- Warning alert: burn rate > 3x over 24h → slow, sustained degradation

Both conditions must fire together (multi-window AND logic) to reduce false positives.

---

### 5. `SLOReportOperator`

Generates a compliance report for one or more SLOs over a specified period. Used in monthly review DAGs or triggered manually for stakeholder reporting.

```python
from airflow.providers.pipeline_slo.operators.slo_report import SLOReportOperator

report = SLOReportOperator(
    task_id="generate_monthly_slo_report",
    slo_ids=["orders_pipeline_latency", "orders_pipeline_freshness"],
    period_days=30,
    output_format="markdown",        # or: "html", "json", "pdf"
    include_sections=[
        "summary",                   # compliance %, budget consumed
        "incidents",                 # individual breaches with duration
        "burn_rate_history",         # burn rate chart data
        "annotations",               # maintenance windows and exclusions
        "budget_forecast",           # extrapolated budget exhaustion date at current rate
    ],
    destination_xcom_key="slo_report",
    # Optional: email or Slack the report
    notify_hook_conn_id="slack_data_leads",
)
```

**Budget forecast:** The report includes an extrapolated budget exhaustion date based on the trailing 7-day burn rate. If the current burn rate is 2.5x, the report warns: "At the current burn rate, error budget will be exhausted in approximately 12 days (April 7)."

---

## Sensors

### `SLOBudgetRemainingSensor`

Waits until error budget rises above a threshold (e.g., after the rolling window moves past a cluster of failures from last month). Useful in DAGs that should only run high-risk operations when budget is healthy.

```python
from airflow.providers.pipeline_slo.sensors.slo_budget_remaining import SLOBudgetRemainingSensor

wait_for_budget = SLOBudgetRemainingSensor(
    task_id="wait_for_budget_recovery",
    slo_id="orders_pipeline_latency",
    min_budget_percent=20,
    timeout=86400,          # wait up to 24 hours for budget to recover
    poke_interval=3600,     # check hourly
)
```

### `SLOBurnRateSensor`

Blocks downstream tasks until the burn rate drops below a threshold. Used to pause risky batch jobs during active incidents.

```python
from airflow.providers.pipeline_slo.sensors.slo_burn_rate import SLOBurnRateSensor

wait_for_calm = SLOBurnRateSensor(
    task_id="wait_for_burn_rate_normal",
    slo_id="orders_pipeline_latency",
    max_burn_rate=2.0,       # wait until burn rate < 2x normal
    window_hours=1,
    timeout=7200,
    poke_interval=300,
)
```

### `SLOCompliantSensor`

Checks that an SLO is currently in compliance (SLI >= target over the rolling window). Used to gate dependent DAGs on upstream SLO health.

```python
from airflow.providers.pipeline_slo.sensors.slo_compliant import SLOCompliantSensor

check_upstream = SLOCompliantSensor(
    task_id="check_upstream_slo",
    slo_id="orders_pipeline_latency",
    timeout=300,
    soft_fail=True,    # warn, don't fail, if upstream is out of SLO
)
```

---

## Hooks

### `PagerDutyHook`

Fires PagerDuty incidents for critical burn rate alerts, with automatic deduplication and auto-resolution when burn rate normalizes.

```python
from airflow.providers.pipeline_slo.hooks.pagerduty import PagerDutyHook

hook = PagerDutyHook(conn_id="pagerduty_default")

hook.fire_alert(
    slo_id="orders_pipeline_latency",
    burn_rate=14.6,
    window_hours=1,
    budget_remaining_percent=42.0,
    severity="critical",
    dedup_key="slo:orders_pipeline_latency:burn_rate",
)

hook.resolve_alert(dedup_key="slo:orders_pipeline_latency:burn_rate")
```

**Auto-resolution:** When `BurnRateAlertOperator` evaluates burn rates and finds they have returned below threshold, it calls `resolve_alert()` on all hooks — PagerDuty incidents auto-resolve without human intervention.

### `PrometheusHook`

Pushes SLI measurements and error budget metrics to a Prometheus Pushgateway. Enables Grafana dashboards for teams that already use the Prometheus stack.

```python
from airflow.providers.pipeline_slo.hooks.prometheus import PrometheusHook

hook = PrometheusHook(conn_id="prometheus_pushgateway_default")
hook.push_metrics(
    slo_id="orders_pipeline_latency",
    metrics={
        "airflow_slo_sli_value": 0.997,
        "airflow_slo_budget_remaining_seconds": 129600,
        "airflow_slo_budget_remaining_percent": 60.0,
        "airflow_slo_burn_rate_1h": 0.8,
        "airflow_slo_burn_rate_6h": 1.1,
        "airflow_slo_compliant": 1,
    },
    labels={"dag_id": "orders_pipeline", "slo_id": "orders_pipeline_latency"},
)
```

### `DatadogHook`

Submits SLO metrics as Datadog custom metrics and optionally creates/updates Datadog SLOs (Datadog's native SLO feature) to mirror Airflow's SLO state.

### `OpsGenieHook`

Fires OpsGenie alerts for warning-level burn rate events, with team routing based on DAG tags.

### `SlackSLOHook`

Posts structured SLO status messages to Slack with burn rate, budget remaining, and a deep link to the Airflow SLO dashboard. Supports block kit formatting.

---

## SLO Store

Backed by Airflow's metadata DB. No new external dependencies for core functionality; Prometheus/Datadog are optional push destinations.

### Schema

```sql
-- SLO definitions: the source of truth for all SLOs
CREATE TABLE slo_definitions (
    id              VARCHAR(250) PRIMARY KEY,
    dag_id          VARCHAR(250) NOT NULL,
    description     TEXT,
    sli_type        VARCHAR(50) NOT NULL,   -- latency, success_rate, freshness
    target          FLOAT NOT NULL,         -- e.g. 0.995
    window_days     INTEGER NOT NULL,       -- rolling window in days
    config          JSONB NOT NULL,         -- SLI-type-specific config
    alerting        JSONB,
    budget_policy   JSONB,
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Error budget ledger: one row per DAG run SLI measurement
CREATE TABLE slo_budget_ledger (
    id              SERIAL PRIMARY KEY,
    slo_id          VARCHAR(250) NOT NULL REFERENCES slo_definitions(id),
    dag_id          VARCHAR(250) NOT NULL,
    run_id          VARCHAR(250) NOT NULL,
    execution_date  TIMESTAMP NOT NULL,
    sli_value       FLOAT NOT NULL,         -- measured SLI for this run (0 or 1 for binary)
    sli_good        BOOLEAN NOT NULL,       -- did this run satisfy the SLI?
    latency_seconds FLOAT,                  -- actual duration (for latency SLIs)
    freshness_seconds FLOAT,                -- actual data age (for freshness SLIs)
    excluded        BOOLEAN DEFAULT FALSE,  -- true if annotated as maintenance
    annotation_id   INTEGER,
    recorded_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Burn rate log: time-series of computed burn rates per SLO
CREATE TABLE slo_burn_rate_log (
    id              SERIAL PRIMARY KEY,
    slo_id          VARCHAR(250) NOT NULL REFERENCES slo_definitions(id),
    window_hours    INTEGER NOT NULL,
    burn_rate       FLOAT NOT NULL,
    budget_remaining_percent FLOAT NOT NULL,
    alerted         BOOLEAN DEFAULT FALSE,
    computed_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Annotations: maintenance windows and incident exclusions
CREATE TABLE slo_annotations (
    id              SERIAL PRIMARY KEY,
    slo_id          VARCHAR(250) NOT NULL REFERENCES slo_definitions(id),
    dag_id          VARCHAR(250) NOT NULL,
    run_id          VARCHAR(250),            -- null = applies to time range
    annotation_type VARCHAR(50) NOT NULL,   -- excluded, degraded, informational
    reason          TEXT NOT NULL,
    approved_by     VARCHAR(250),
    valid_from      TIMESTAMP NOT NULL,
    valid_to        TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## SLOResult — XCom model

Every `SLOCheckpointOperator` and `BurnRateAlertOperator` pushes a typed `SLOResult` to XCom:

```python
@dataclass
class SLOResult:
    slo_id: str
    dag_id: str
    run_id: str
    execution_date: datetime

    # SLI measurement for this run
    sli_value: float
    sli_good: bool

    # Rolling window state
    window_days: int
    compliance_percent: float        # SLI over rolling window (e.g. 99.7%)
    target_percent: float            # SLO target (e.g. 99.5%)
    compliant: bool                  # compliance_percent >= target_percent

    # Error budget
    budget_total_minutes: float      # total allowed failure time in window
    budget_consumed_minutes: float   # failure time consumed so far
    budget_remaining_minutes: float
    budget_remaining_percent: float

    # Burn rates (multi-window)
    burn_rate_1h: float | None
    burn_rate_6h: float | None
    burn_rate_24h: float | None
    burn_rate_72h: float | None

    # Budget forecast
    projected_exhaustion_date: datetime | None  # None if compliant

    # Alert state
    alerts_fired: list[str]          # hook conn_ids that were alerted

    def raise_if_breached(self) -> None: ...
    def is_budget_critical(self, threshold_percent: float = 10.0) -> bool: ...
    def summary(self) -> str: ...    # human-readable one-liner

    @classmethod
    def from_xcom(cls, context: dict, task_id: str) -> "SLOResult": ...
```

---

## The SLI Engine

The core computation layer. Lives in `engine/` and is used by all operators and the listener.

### SLI Calculator

```python
from airflow.providers.pipeline_slo.engine.sli_calculator import SLICalculator

calc = SLICalculator(slo_id="orders_pipeline_latency", window_days=30)

# Returns fraction of good events in the rolling window
sli_value = calc.compute()    # e.g. 0.9972

# Returns all individual measurements in the window
measurements = calc.get_window_measurements()

# Returns count of good vs bad events
good, total = calc.count_events()
```

### Burn Rate Engine

```python
from airflow.providers.pipeline_slo.engine.burn_rate_engine import BurnRateEngine

engine = BurnRateEngine(slo_id="orders_pipeline_latency")

# Compute burn rate for a specific window
burn_1h = engine.compute(window_hours=1)
# burn_rate = error_rate_in_window / (1 - slo_target)
# e.g. error_rate = 0.05, slo_target = 0.995 → burn_rate = 0.05/0.005 = 10x

# Check all configured windows
rates = engine.compute_all()
# {"1h": 10.2, "6h": 4.1, "24h": 1.8, "72h": 1.1}

# Should we alert?
alerts = engine.evaluate_thresholds()
# [{"window": "1h", "burn_rate": 10.2, "threshold": 14.4, "exceeded": False}]
```

---

## DAG Run Listener — Zero-Touch SLI Recording

For teams that cannot or do not want to modify their existing DAGs, the provider ships a listener that automatically records SLI measurements:

```python
# registered via provider.yaml — no DAG changes required
from airflow.providers.pipeline_slo.listeners.dag_run_listener import SLODagRunListener
```

The listener hooks into Airflow's plugin listener API and fires on:
- `on_dag_run_success` — records a "good" event for all SLOs registered for this `dag_id`
- `on_dag_run_failed` — records a "bad" event
- `on_dag_run_running` — starts a latency timer

For latency SLIs, the listener measures `dag_run.end_date - dag_run.start_date` and compares against `latency_target_minutes`.

This means a team can define an SLO in YAML, install the provider, and immediately get SLI tracking — without touching a single DAG file.

---

## Example DAGs

### Pattern 1: Checkpoint at the end (most common)

```python
from datetime import datetime
from airflow.decorators import dag, task
from airflow.providers.pipeline_slo.operators.slo_checkpoint import SLOCheckpointOperator
from airflow.providers.pipeline_slo.operators.error_budget_guard import ErrorBudgetGuardOperator

@dag(schedule="0 5 * * *", start_date=datetime(2026, 1, 1))
def orders_pipeline():

    @task
    def extract(): ...

    @task
    def transform(data): ...

    @task
    def load(data):
        return {"data_as_of_timestamp": datetime.utcnow().isoformat()}

    # Guard blocks the load if budget is critically low
    guard = ErrorBudgetGuardOperator(
        task_id="budget_guard",
        slo_id="orders_pipeline_latency",
        min_budget_percent=10,
        on_insufficient="fail",
    )

    # Checkpoint measures end-to-end latency and updates the ledger
    checkpoint = SLOCheckpointOperator(
        task_id="slo_checkpoint",
        slo_ids=["orders_pipeline_latency", "orders_pipeline_freshness"],
        freshness_xcom_task_id="load",
        freshness_xcom_key="data_as_of_timestamp",
        on_breach="warn",
    )

    raw = extract()
    clean = transform(raw)
    loaded = load(clean)

    [raw, guard] >> clean >> loaded >> checkpoint

orders_pipeline()
```

### Pattern 2: Continuous burn rate monitoring DAG

This runs independently of business DAGs — it's the always-on monitoring heartbeat.

```python
from datetime import datetime
from airflow.decorators import dag
from airflow.providers.pipeline_slo.operators.burn_rate_alert import BurnRateAlertOperator

@dag(
    schedule="*/5 * * * *",    # every 5 minutes
    start_date=datetime(2026, 1, 1),
    max_active_runs=1,
    tags=["slo", "monitoring"],
)
def slo_burn_rate_monitor():

    BurnRateAlertOperator(
        task_id="evaluate_all_slos",
        slo_ids=None,              # None = evaluate all registered SLOs
        windows=[
            {"hours": 1,  "threshold": 14.4, "severity": "critical"},
            {"hours": 6,  "threshold": 6.0,  "severity": "warning"},
            {"hours": 24, "threshold": 3.0,  "severity": "info"},
        ],
        alert_hooks=["pagerduty_default", "slack_data_oncall", "datadog_default"],
        suppress_if_annotated=True,
    )

slo_burn_rate_monitor()
```

### Pattern 3: Monthly SLO review report

```python
from datetime import datetime
from airflow.decorators import dag, task
from airflow.providers.pipeline_slo.operators.slo_report import SLOReportOperator

@dag(
    schedule="0 9 1 * *",     # 9am on the 1st of each month
    start_date=datetime(2026, 1, 1),
)
def monthly_slo_review():

    report = SLOReportOperator(
        task_id="generate_report",
        slo_ids=None,              # all SLOs
        period_days=30,
        output_format="markdown",
        include_sections=["summary", "incidents", "burn_rate_history",
                          "annotations", "budget_forecast"],
        notify_hook_conn_id="slack_data_leads",
    )

    @task
    def archive_report(report_content: str):
        # Save to S3, Confluence, etc.
        pass

    archive_report(report.output)

monthly_slo_review()
```

### Pattern 4: Dependent DAG gated on upstream SLO

```python
from airflow.providers.pipeline_slo.sensors.slo_compliant import SLOCompliantSensor

@dag(schedule="@hourly", start_date=datetime(2026, 1, 1))
def ml_feature_pipeline():

    # Don't run ML features if the orders pipeline is out of SLO
    # (its data would be unreliable input)
    check_upstream = SLOCompliantSensor(
        task_id="check_orders_slo",
        slo_id="orders_pipeline_latency",
        soft_fail=True,    # warn but proceed — feature pipeline can degrade gracefully
    )

    @task
    def compute_features(): ...

    check_upstream >> compute_features()
```

---

## UI Extension — SLO Dashboard

An Airflow plugin adds an **SLOs** tab with four panels:

### Error budget gauges
One gauge per SLO showing budget remaining as a percentage of the 30-day allocation. Color bands: green (>25%), amber (10–25%), red (<10%). Clicking a gauge opens the detail view.

### Burn rate chart
Time-series of burn rates across all configured windows for the selected SLO. Horizontal threshold lines at configured alert levels. Annotated with vertical markers for maintenance windows and incidents.

### Compliance heatmap
Calendar heatmap (GitHub contribution graph style) where each cell is a DAG run, colored by SLI outcome: green (good), red (bad), gray (excluded/annotated). Shows at a glance whether failures cluster on weekends, month-ends, or after deployments.

### Budget forecast table
Per-SLO table showing: current compliance %, budget remaining, trailing 7-day burn rate, and projected exhaustion date. Sortable by risk.

---

## CLI Extension

```bash
# Show status of all SLOs
airflow slo status

# Show status of a specific SLO
airflow slo status --slo-id orders_pipeline_latency

# Show current error budget
airflow slo budget --slo-id orders_pipeline_latency

# Show burn rates across windows
airflow slo burn-rate --slo-id orders_pipeline_latency

# List all SLO breaches in the last 30 days
airflow slo incidents --slo-id orders_pipeline_latency --days 30

# Annotate a run as maintenance (exclude from SLI calculation)
airflow slo annotate \
  --slo-id orders_pipeline_latency \
  --run-id scheduled__2026-03-26T05:00:00 \
  --type excluded \
  --reason "Snowflake planned maintenance" \
  --approved-by alice

# Generate a report
airflow slo report --slo-id orders_pipeline_latency --days 30 --format markdown

# Validate a YAML SLO definition file
airflow slo validate slos/orders_pipeline.yaml

# Register or update SLOs from a YAML file
airflow slo sync slos/orders_pipeline.yaml
```

---

## Backwards Compatibility

This is a new provider package. Fully additive. No changes to Airflow core.

Schema additions are applied via Alembic migrations only when the provider is installed.

The DAG run listener is opt-in — it must be explicitly enabled either via `provider.yaml` or a config flag. Existing DAGs are unaffected unless teams choose to add `SLOCheckpointOperator` tasks.

---

## Implementation Plan

### Phase 1 — Core SLI + budget (v1.0)
- `SLODefinition` model + YAML loader + `airflow slo sync/validate` commands
- Error Budget Ledger schema + migrations
- `SLICalculator` + `BurnRateEngine` (pure Python, no external deps)
- `SLOCheckpointOperator` (latency + success_rate SLI types)
- `ErrorBudgetGuardOperator`
- `SLOResult` XCom model
- DAG run listener (zero-touch recording)
- Unit tests with synthetic SLI data

### Phase 2 — Alerting + sensors (v1.1)
- `BurnRateAlertOperator` with multi-window logic
- `PagerDutyHook` + `SlackSLOHook` with auto-resolution
- `SLOBurnRateSensor`, `SLOCompliantSensor`, `SLOBudgetRemainingSensor`
- `SLOAnnotateOperator`
- Freshness SLI type in `SLOCheckpointOperator`

### Phase 3 — Integrations + reporting (v1.2)
- `PrometheusHook`, `DatadogHook`, `OpsGenieHook`
- `SLOReportOperator` (markdown + JSON output)
- Budget forecast calculation
- SLO Dashboard UI plugin (error budget gauges, burn rate chart, heatmap)

### Phase 4 — Advanced (v2.0)
- Custom SLI types via plugin interface
- SLO composition (composite SLOs spanning multiple DAGs)
- `airflow slo forecast` — ML-based budget exhaustion prediction
- Integration with `apache-airflow-provider-pipeline-slo` → alert if AI pipeline governance SLO is breaching
- OpenTelemetry export of all SLO metrics
- Grafana dashboard template (JSON) for teams using Prometheus

---

## Alternatives Considered

**Airflow's built-in SLA mechanism (`sla_miss_callback`):** Airflow has a basic SLA feature that fires a callback when a task misses a deadline. It has no concept of error budgets, burn rates, rolling windows, or compliance percentages. It is binary (missed or not) and has no persistence layer. This provider is a complete superset.

**Datadog SLOs / Prometheus SLOs (sloth, pyrra):** These tools define SLOs on time-series metrics from services. They do not understand Airflow DAG runs, execution dates, XCom data, or task-level context. They require exporting metrics to an external system first. This provider works natively inside Airflow — no external metric export needed for basic functionality.

**Manual tracking in spreadsheets:** The status quo. Works until it doesn't, provides no automated alerting, requires human calculation of burn rates, and produces no audit trail.

---

## References

- Google SRE Book, Chapter 4 — Service Level Objectives: https://sre.google/sre-book/service-level-objectives/
- Google SRE Workbook, Chapter 5 — Alerting on SLOs: https://sre.google/workbook/alerting-on-slos/
- Airflow SLA docs: https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/tasks.html#slas
- sloth (Prometheus SLO tool): https://github.com/slok/sloth
- pyrra (Kubernetes SLO tool): https://github.com/pyrra-dev/pyrra
- OpenSLO specification: https://openslo.com/
