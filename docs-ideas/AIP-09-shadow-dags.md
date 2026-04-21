# AIP-09: Shadow DAGs — Safe Experiments in Production

| Field | Value |
|---|---|
| **Author(s)** | Platform Engineering — Data Infrastructure Team |
| **Status** | Draft |
| **Type** | Feature |
| **Target** | Airflow 3.x + internal orchestration layer |
| **Created** | 2026-04-20 |
| **Replaces** | None |

---

## Contents

1. [Abstract](#1-abstract)
2. [Motivation](#2-motivation)
3. [Goals & Non-Goals](#3-goals--non-goals)
4. [Proposal](#4-proposal)
5. [Shadow DAG Lifecycle](#5-shadow-dag-lifecycle)
6. [Configuration API](#6-configuration-api)
7. [Result Comparison](#7-result-comparison)
8. [Observability](#8-observability)
9. [Alternatives Considered](#9-alternatives-considered)
10. [Risks & Mitigations](#10-risks--mitigations)
11. [Rollout Plan](#11-rollout-plan)
12. [References](#12-references)

---

## 1. Abstract

This proposal introduces **Shadow DAGs**: a first-class mechanism for safely running experimental pipeline code alongside production DAGs. A shadow execution reads from the same real data sources as its production counterpart but writes all output to isolated, non-production sinks. Differences between shadow and production outputs are captured in a structured comparison report and surfaced through existing observability tooling.

Shadow DAGs enable engineers to validate new transformation logic, dependency changes, and infrastructure migrations at full production load and data fidelity — without any risk of corrupting production state.

---

## 2. Motivation

### The staging gap problem

Staging environments reduce production incidents but cannot eliminate them. The gap between staging and production manifests as subtle data skew, volume differences, timing sensitivities, and third-party API behaviors that are difficult or impossible to replicate. The result is a recurring pattern: a DAG passes all staging tests, is promoted to production, and immediately produces incorrect outputs on real data.

### The current workaround is dangerous

Today, engineers work around this by deploying experimental code to production behind feature flags, using dry-run modes specific to each individual operator, or running backfills on production data and manually comparing results. These approaches are fragile, inconsistent, and place correctness validation burden on individual engineers rather than on the platform.

> **Incident pattern:** In Q3 2025, 4 of 9 production data pipeline incidents were traceable to untested behavior that only manifested on real production data volume or shape. Shadow DAGs would have caught all four before promotion.

### What we need

A platform-level primitive that any DAG author can use to run candidate code against live production data, automatically isolating all writes, capturing divergences, and reporting results — without any changes to the production DAG itself.

---

## 3. Goals & Non-Goals

### In scope

- Run candidate DAG code in parallel with production at every scheduled run
- Intercept and redirect all writes to shadow sinks automatically
- Compute structured row-level and aggregate diffs between shadow and production outputs
- Surface shadow health via existing Airflow task state and a new Comparison UI
- Time-bound shadow execution with auto-expiry and cleanup
- Support for BigQuery, GCS, Pub/Sub, and Postgres sinks in the initial release
- Configurable divergence alerting thresholds

### Out of scope

- Automatic promotion of shadow DAGs to production
- Shadow execution of external API calls or third-party writes
- Per-task shadowing (shadow mode applies to the whole DAG)
- Real-time streaming pipelines (initial release is batch-only)
- Shadow DAGs that themselves trigger downstream DAGs
- Cross-team shadow DAG management in the initial release

---

## 4. Proposal

### Core concept

A Shadow DAG is a parallel execution unit derived from an existing production DAG. It is created by the engineer via the Airflow CLI or UI and runs automatically alongside every production run for a configured duration. The platform enforces three invariants:

1. **Read parity.** The shadow DAG reads from the same production data sources at the same logical time as the production run.
2. **Write isolation.** All writes performed by shadow tasks are intercepted by a Sink Proxy layer and redirected to ephemeral shadow namespaces. No shadow execution ever touches a production sink.
3. **Comparison fidelity.** A Comparison Engine reads from both the production output and the shadow output after each run and produces a structured diff report.

```
┌─────────────┐    reads    ┌─────────────────┐         ┌─────────────┐
│  Production │ ──────────► │ PRODUCTION RUN  │ ──────► │ Prod Sinks  │
│   Sources   │             │  DAG v42 (live) │         │ (live data) │
│  BQ·GCS·PG  │    reads    └─────────────────┘    │    └─────────────┘
│             │ ──────────► ┌─────────────────┐    │         │
└─────────────┘             │   SHADOW RUN    │    ▼    ┌─────────────┐
                            │ DAG v43-candidate│──────► │ Sink Proxy  │──► Shadow Sinks
                            └─────────────────┘         └─────────────┘    (ephemeral)
                                                               │
                                                               ▼
                                                    ┌──────────────────┐
                                                    │ Comparison Engine│
                                                    │  diff·alert·report│
                                                    └──────────────────┘
```

*Both lanes read real production data. Only shadow writes are redirected. The Comparison Engine reads from both sinks after each run.*

### Sink Proxy layer

The Sink Proxy is a thin wrapper injected at DAG parse time for shadow runs. It overrides each supported operator's write hook to redirect the destination to the shadow namespace. The namespace follows a deterministic pattern:

```python
# Shadow sink naming convention
# BigQuery:  {project}.shadow_{dag_id}_{run_id}.{table}
# GCS:       gs://shadow-{bucket}/shadow_{dag_id}/{run_id}/{object}
# Postgres:  shadow_{schema}.{table}  (same cluster, isolated schema)

class SinkProxy:
    def wrap(self, operator: BaseOperator, shadow_ctx: ShadowContext) -> BaseOperator:
        if isinstance(operator, BigQueryInsertJobOperator):
            return _redirect_bq(operator, shadow_ctx)
        elif isinstance(operator, GCSToGCSOperator):
            return _redirect_gcs(operator, shadow_ctx)
        else:
            raise UnsupportedSinkError(f"Operator {type(operator)} not shadow-safe")
```

If an operator is encountered that the Sink Proxy does not recognise, the shadow run fails safe — the task is marked `shadow_blocked` and does not execute. This prevents accidental writes to unsupported sinks.

---

## 5. Shadow DAG Lifecycle

A shadow DAG passes through five phases from creation to cleanup.

```
REGISTERED → ACTIVE → REVIEW → PROMOTED / DISCARDED → CLEANED_UP
  (instant)  (1–14d)  (72h)        (manual)            (automated)
```

| Phase | Trigger | System action | Owner action |
|---|---|---|---|
| `REGISTERED` | `airflow shadow create` | Validate DAG file, create shadow sinks, register metadata record | Submit shadow config |
| `ACTIVE` | Next scheduled production run | Spawn parallel shadow task tree; proxy all writes | Monitor comparison reports |
| `REVIEW` | Expiry date reached or manual trigger | Halt new shadow runs; preserve sink data; alert owner | Evaluate comparison summary |
| `PROMOTED` | `airflow shadow promote` | Create PR / deployment ticket; archive shadow data | Deploy candidate to production |
| `CLEANED_UP` | 72h after REVIEW or manual discard | Drop shadow datasets, delete sink data, archive logs | — |

> **Auto-expiry:** Shadow DAGs have a maximum TTL of 14 days, configurable down to 1 day. An engineer must explicitly extend a shadow beyond 14 days with documented justification. This prevents accumulation of long-running shadows that inflate compute costs.

---

## 6. Configuration API

### CLI

```bash
# Register a shadow DAG from a candidate DAG file
airflow shadow create \
  --production-dag   etl.orders_daily \
  --candidate-file   dags/orders_daily_v2.py \
  --ttl              7d \
  --divergence-alert 5% \
  --notify           data-eng-oncall@company.com

# List active shadows
airflow shadow list

# Show comparison report for latest run
airflow shadow report --shadow-id shd_orders_daily_20260420

# Promote (creates deployment PR, archives shadow data)
airflow shadow promote --shadow-id shd_orders_daily_20260420

# Discard and clean up
airflow shadow discard --shadow-id shd_orders_daily_20260420
```

### Python decorator API

```python
from airflow.shadow import shadow_dag

# Mark a DAG as a shadow candidate inline.
# The shadow decorator registers it automatically on deploy.
@shadow_dag(
    shadows="etl.orders_daily",
    ttl="7d",
    divergence_alert=0.05,
)
@dag(schedule="@daily", catchup=False)
def orders_daily_v2():
    # Tasks defined here run in shadow mode only.
    # All sinks are automatically redirected.
    ...
```

---

## 7. Result Comparison

After each shadow run completes, the Comparison Engine runs as a post-task hook. It queries both the production output sink and the shadow sink and produces a **Comparison Report** structured as follows:

| Field | Type | Description |
|---|---|---|
| `run_id` | string | Airflow run ID of the compared execution |
| `row_count_prod` | int | Row count in production sink |
| `row_count_shadow` | int | Row count in shadow sink |
| `row_count_delta_pct` | float | Percentage difference in row counts |
| `schema_divergence` | list[ColumnDiff] | Added, removed, or type-changed columns |
| `value_divergence` | list[FieldStats] | Per-column null rate, min, max, mean, p95 divergence |
| `sample_diff_rows` | list[Row] | Up to 100 rows where shadow and production disagree |
| `verdict` | enum | `MATCH` · `WITHIN_THRESHOLD` · `DIVERGED` · `SHADOW_FAILED` |

The `verdict` field drives alerting. A `DIVERGED` verdict fires the notification channel specified at shadow creation time. A `SHADOW_FAILED` verdict — triggered when the shadow task tree fails entirely — is treated as a signal that the candidate code is not production-ready, but does not affect production health.

> **Key invariant:** A shadow run failure is always isolated. Production task state, SLAs, and downstream dependencies are never affected by shadow execution outcomes.

---

## 8. Observability

### Airflow UI extensions

Two surfaces are added to the Airflow web UI. First, a **Shadow Lane** appears below each production DAG run in the Grid view, showing shadow task state with a distinct amber border. Shadow tasks are visually distinct but use the same state colour conventions (green = success, red = failed, etc.).

Second, a new **Shadow Reports** tab on the DAG detail page shows the time-series of verdict outcomes across all shadow runs for that DAG, with links to per-run comparison reports.

### Metrics emitted

| Metric | Labels | Description |
|---|---|---|
| `shadow.run.duration_seconds` | dag_id, shadow_id | Wall-clock duration of shadow task tree |
| `shadow.run.verdict` | dag_id, shadow_id, verdict | Counter by verdict outcome |
| `shadow.row_delta_pct` | dag_id, shadow_id | Row count divergence gauge |
| `shadow.compute_overhead_pct` | dag_id | Shadow compute cost as % of production run |

### Cost overhead

Shadow runs consume real compute. The platform emits `shadow.compute_overhead_pct` to track this. Teams are expected to keep shadow overhead below 20% of production compute cost for any given DAG. The shadow registry will warn when a shadow is estimated to exceed this threshold based on past run durations.

---

## 9. Alternatives Considered

| Alternative | Why rejected |
|---|---|
| **Dedicated shadow environment** | Requires full data replication. Expensive, stale data, and does not replicate production IAM or network topology. Misses the class of bugs that only appear on real data shape. |
| **Operator-level dry-run flags** | Not universal — each operator must implement its own dry-run semantics. Inconsistent coverage and no cross-operator comparison framework. |
| **Canary DAG runs (% traffic split)** | Requires changes to upstream producers. Leaks candidate logic into real downstream consumers. Not appropriate for correctness testing. |
| **Manual backfill comparison** | Current practice. Time-consuming, error-prone, requires manual diffing, and does not run at the same wall-clock time as the production run (missing timing-sensitive issues). |

---

## 10. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Shadow write escapes isolation and touches a production sink | Low | Sink Proxy enforces allow-list of redirectable sink types. Unknown operators fail safe (blocked, not skipped). Integration test suite covers all supported sink types. |
| Shadow run delays production run due to shared worker pool contention | Medium | Shadow tasks run in a dedicated worker queue with hard concurrency limits. Production tasks always have priority scheduling. |
| Shadow sinks accumulate unbounded data and inflate storage costs | Medium | Automatic TTL on shadow datasets. Storage budget per shadow capped at 10× production daily output. Registry enforces this on creation. |
| Comparison Engine produces false-positive divergence reports | Medium | Configurable tolerance thresholds per field type. Timestamp and UUID columns excluded from value comparison by default. |
| Shadow mechanism used as a way to avoid proper staging discipline | Low | Shadow DAGs are not a replacement for staging validation. Documentation and onboarding materials make this explicit. |

> **Hard constraint:** Shadow DAGs must never be used to shadow DAGs that perform financial transactions, send customer-facing notifications, or mutate billing records — even if sinks are correctly proxied. The Sink Proxy cannot intercept side-effects in task logic itself (e.g., a Python callable that calls a payment API directly).

---

## 11. Rollout Plan

Shadow DAG support will be rolled out in three phases over approximately 10 weeks.

### Phase 1 — Internal pilot (weeks 1–4)

Deploy to the internal data infrastructure team's DAGs only. Focus on BigQuery and GCS sink support. Validate the Sink Proxy, comparison engine, and TTL cleanup machinery. Collect feedback on comparison report UX. Target: 3 shadow DAGs running concurrently.

### Phase 2 — Expanded beta (weeks 5–8)

Open to all platform engineering teams. Add Postgres sink support. Ship the Shadow Lane UI to Airflow Grid view. Establish cost overhead guardrails based on Phase 1 data. Target: up to 20 shadow DAGs running concurrently.

### Phase 3 — General availability (weeks 9–10)

Open to all teams. Ship the `@shadow_dag` decorator API. Publish documentation and runbooks. Enable shadow compute overhead metrics in the cost dashboard. Establish on-call escalation path for Sink Proxy incidents.

> **Deprecation note:** Following GA of Shadow DAGs, the platform team will deprecate the documented "manual backfill comparison" practice in favour of Shadow DAGs as the canonical pre-production validation mechanism for batch pipeline changes.

---

## 12. References

- Fowler, M. (2004). *Strangler Fig Application* — foundational pattern for parallel execution of old and new code.
- GitHub Engineering Blog — *Scientist: carefully refactor critical paths* (Ruby library for production shadowing).
- Shopify Engineering — *Shadow Mode Testing*, illustrating write isolation patterns at scale.
- AIP-1: *DAG Authoring Conventions* — defines the DAG decorator protocol this proposal extends.
- Internal: *Q3 2025 Incident Report — Pipeline Regressions* (link in internal wiki).
- Internal: *Staging Environment Limitations Analysis* (internal wiki, Data Infra team, 2025-11).

---

*AIP-09 · Shadow DAGs · Draft · 2026-04-20 · Data Infrastructure*
