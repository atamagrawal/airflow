# AIP-DC: Data Contract Provider — Catalog-Backed Contract Enforcement in Airflow

**Author:** [Your Name]
**Status:** Draft
**Created:** 2026-03-26
**Category:** Provider
**Discussions-To:** https://github.com/apache/airflow/discussions

---

## Abstract

This AIP proposes `apache-airflow-provider-data-contracts` — a provider that makes
data contracts first-class citizens in Apache Airflow DAGs, backed by a data catalog
(DataHub, OpenMetadata, Alation, Collibra, or Atlan) as the source of truth.

DataHub defines and stores contracts: schema expectations, SLA promises, freshness
requirements, ownership, and compatibility rules. Airflow enforces them: validating
that a dataset satisfies its contract before it flows downstream, blocking pipelines
when contracts are breached, and reporting violations back to the catalog so the
entire organization sees pipeline health in one place.

This is the "Pact for data pipelines" — the missing layer between data catalog
governance and actual pipeline execution.

---

## Motivation

### What a data contract is

A data contract is a formal, versioned agreement between a data producer and a data
consumer. It declares:

- **Schema**: field names, types, nullability, primary keys
- **Freshness**: "this dataset will be updated by 6am daily"
- **Completeness**: "this dataset will have at least 1 million rows after each run"
- **SLA**: "the pipeline producing this dataset will complete within 45 minutes"
- **Ownership**: who is responsible for the producer, who depends on it as a consumer
- **Compatibility**: what schema changes are breaking vs non-breaking

### Where contracts live today

Data catalogs like DataHub, OpenMetadata, and Atlan have begun supporting contract
definitions. You can declare a contract in YAML or through a UI. But the contract
is inert — it is documentation, not enforcement. Nothing actually checks whether the
pipeline honored the contract at runtime. Nothing blocks a consumer DAG when the
producer violated the contract. Nothing writes a breach back to the catalog.

### What Airflow is missing

Airflow runs the pipelines that produce and consume data. It is the natural enforcement
point — it controls task execution, knows the output of every task via XCom, and
manages dependencies between producers and consumers. But Airflow has no concept of
a contract. It does not know that `orders_table` has an owner, a schema promise, or
a freshness SLA. It just runs tasks.

### The analogy

| | Great Expectations | This provider |
|---|---|---|
| What it checks | Is the data correct? | Was the promise kept? |
| Where rules live | Expectation suites in JSON/YAML | Contracts in DataHub/catalog |
| Who defines rules | Data engineers | Data owners + consumers (via catalog UI) |
| Enforcement | Task-level assertion | Task-level + cross-DAG dependency |
| Breach visibility | Airflow logs | Airflow logs + catalog incident feed |

Great Expectations is about data quality. This provider is about data trust —
did the producer deliver what the consumer was promised?

---

## Goals

1. Fetch contract definitions from a data catalog (DataHub-first, others supported)
2. Validate pipeline outputs against those contracts at task boundaries
3. Block consumer DAGs when their upstream contract is breached
4. Push breach events, schema changes, and lineage back to the catalog
5. Support schema evolution with backward/forward compatibility checking
6. Provide a catalog-agnostic hook interface so teams are not locked to DataHub
7. Extend the Airflow UI with a Contract Health panel
8. Expose `airflow contracts` CLI for contract inspection and manual breach resolution

## Non-Goals

- Replacing the data catalog (DataHub owns contracts, Airflow enforces them)
- Writing contract definitions (teams use the catalog UI or YAML files committed
  alongside DAGs — Airflow reads them, never owns them)
- Row-level data quality (Great Expectations does this; this provider is orthogonal)

---

## Core Design: Catalog as Source of Truth

```
┌─────────────────────────────┐       ┌──────────────────────────────┐
│       DataHub catalog        │       │       Apache Airflow          │
│                              │       │                              │
│  Dataset: orders_table       │◄──────│  ContractPublishOperator     │
│  Contract v3:                │       │  (writes lineage + status)   │
│    schema: ...               │       │                              │
│    freshness: by 6am daily   │──────►│  ContractValidateOperator    │
│    owner: data-platform      │       │  (reads + enforces contract) │
│    consumers: [ml, finance]  │       │                              │
│  Status: ACTIVE              │◄──────│  ContractBreachGuard         │
│  Last breach: never          │       │  (writes breach event)       │
└─────────────────────────────┘       └──────────────────────────────┘
```

Airflow never stores contracts. It fetches them from the catalog at DAG run time,
validates the pipeline output against them, and writes results back. The catalog
remains the single source of truth visible to everyone — data engineers, data
scientists, analysts, and business stakeholders.

---

## Proposed Design

### Package structure

```
apache-airflow-provider-data-contracts/
├── airflow/
│   └── providers/
│       └── data_contracts/
│           ├── __init__.py
│           ├── operators/
│           │   ├── contract_validate.py
│           │   ├── contract_publish.py
│           │   ├── contract_breach_guard.py
│           │   ├── schema_evolution.py
│           │   └── contract_report.py
│           ├── sensors/
│           │   ├── contract_ready.py
│           │   └── schema_match.py
│           ├── hooks/
│           │   ├── base_catalog.py          # abstract base
│           │   ├── datahub.py               # DataHub GMS REST API
│           │   ├── open_metadata.py
│           │   ├── atlan.py
│           │   ├── collibra.py
│           │   └── alation.py
│           ├── store/
│           │   ├── breach_log.py
│           │   └── contract_version_cache.py
│           ├── models/
│           │   ├── contract.py              # DataContract dataclass
│           │   ├── contract_result.py       # XCom envelope
│           │   └── schema_diff.py
│           ├── validators/
│           │   ├── schema_validator.py
│           │   ├── freshness_validator.py
│           │   ├── completeness_validator.py
│           │   └── sla_validator.py
│           └── ui/
│               └── contract_health_plugin.py
├── tests/
└── provider.yaml
```

---

## The DataContract Model

The central model that all operators work with. Fetched from the catalog and
deserialized into a typed Python object.

```python
@dataclass
class DataContract:
    # Identity
    contract_id: str             # DataHub URN or catalog-native ID
    dataset_urn: str             # e.g. "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders,PROD)"
    dataset_name: str            # human-readable name
    version: int                 # contract version (increments on any change)
    status: str                  # ACTIVE | BREACHED | DEPRECATED | DRAFT

    # Schema contract
    schema: list[SchemaField]    # expected fields with types + nullability
    schema_compatibility: str    # BACKWARD | FORWARD | FULL | NONE
    schema_version: int

    # Freshness contract
    freshness_cron: str | None   # expected update schedule, e.g. "0 6 * * *"
    freshness_max_age_minutes: int | None  # max allowed data age

    # Completeness contract
    min_row_count: int | None
    max_row_count: int | None
    required_partitions: list[str] | None

    # SLA contract
    sla_completion_minutes: int | None   # pipeline must finish within N minutes

    # Ownership
    producer_team: str
    producer_dag_id: str | None
    consumer_teams: list[str]
    consumer_dag_ids: list[str]
    data_steward: str | None

    # Catalog metadata
    catalog_url: str             # deep link back to the catalog entry
    last_validated_at: datetime | None
    last_breach_at: datetime | None
    tags: list[str]

    def get_schema_field(self, name: str) -> SchemaField | None: ...
    def is_breaking_change(self, new_schema: list[SchemaField]) -> bool: ...
    def validate_row_count(self, actual: int) -> ContractViolation | None: ...


@dataclass
class SchemaField:
    name: str
    type: str                    # STRING, INTEGER, FLOAT, BOOLEAN, DATE, STRUCT, ARRAY
    nullable: bool
    description: str | None
    primary_key: bool
    partition_key: bool
    tags: list[str]


@dataclass
class ContractViolation:
    violation_type: str          # SCHEMA_MISMATCH | FRESHNESS | COMPLETENESS | SLA
    severity: str                # CRITICAL | WARNING
    expected: str
    actual: str
    field_name: str | None       # for schema violations
    message: str
```

---

## Operators

### 1. `ContractValidateOperator`

The core operator. Fetches the contract for a dataset from the catalog, validates
the pipeline's output against every clause (schema, freshness, completeness, SLA),
and either fails the task, warns, or passes — depending on severity and configuration.

```python
from airflow.providers.data_contracts.operators.contract_validate import ContractValidateOperator

validate = ContractValidateOperator(
    task_id="validate_orders_contract",
    catalog_conn_id="datahub_default",

    # Which dataset's contract to enforce
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)",
    # or by name if catalog supports it:
    # dataset_name="prod.orders",

    # Where to get actual data stats for validation
    # Option A: from XCom (previous task emitted stats)
    stats_xcom_task_id="load_orders",
    stats_xcom_key="output_stats",   # expects: {row_count, schema, data_as_of}

    # Option B: query the target directly (provider fetches stats itself)
    # target_conn_id="snowflake_default",
    # target_table="prod.orders",

    # Which contract clauses to enforce
    validate_schema=True,
    validate_freshness=True,
    validate_completeness=True,
    validate_sla=True,

    # Failure behavior per violation type
    on_schema_violation="fail",        # hard failure — wrong schema is always critical
    on_freshness_violation="warn",     # warn — freshness might be legitimately late
    on_completeness_violation="fail",
    on_sla_violation="warn",

    # Write breach back to catalog
    report_breach_to_catalog=True,

    # Push result for downstream tasks
    result_xcom_key="contract_result",
)
```

**Execution flow:**

```
1. Fetch contract from DataHub GMS API
   GET /entities?urn=...  → DataContract object

2. Fetch actual output stats
   - From XCom: row_count, column list, data_as_of_timestamp
   - Or: query target system directly

3. Validate each clause:
   a. Schema: compare actual columns vs contract schema
      - Missing required fields → CRITICAL
      - Type mismatch → CRITICAL
      - Extra unexpected fields → WARNING (depends on compatibility mode)
      - Nullability violation → WARNING or CRITICAL per config

   b. Freshness: compare data_as_of_timestamp vs now
      - Age > freshness_max_age_minutes → CRITICAL
      - Run missed its cron window → WARNING

   c. Completeness: compare actual row_count
      - Below min_row_count → CRITICAL
      - Above max_row_count → WARNING

   d. SLA: compare (execution_date → now) vs sla_completion_minutes
      - Exceeded → WARNING

4. Collect all ContractViolation objects

5. Report breach to catalog if any CRITICAL violations
   POST /aspects → contractViolation aspect on dataset URN

6. Push ContractResult to XCom

7. Raise AirflowException if any "fail" violations exist
```

---

### 2. `ContractPublishOperator`

The outbound operator — called by the producer DAG after successfully writing a
dataset. It writes lineage, updates contract status to ACTIVE, and stamps the
"last_validated_at" on the dataset in the catalog.

```python
from airflow.providers.data_contracts.operators.contract_publish import ContractPublishOperator

publish = ContractPublishOperator(
    task_id="publish_orders_contract",
    catalog_conn_id="datahub_default",
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)",

    # Lineage: what did this task read to produce the dataset?
    upstream_urns=[
        "urn:li:dataset:(urn:li:dataPlatform:kafka,raw.orders,PROD)",
        "urn:li:dataset:(urn:li:dataPlatform:postgres,public.customers,PROD)",
    ],

    # Output stats to stamp on the dataset
    stats_xcom_task_id="load_orders",
    stats_xcom_key="output_stats",

    # Mark contract as satisfied
    update_contract_status=True,
    contract_status="ACTIVE",

    # Emit Airflow run metadata to DataHub
    emit_run_facet=True,     # writes dag_id, run_id, execution_date to DataHub
)
```

**What gets written to DataHub:**

```json
{
  "aspect": "datasetProperties",
  "lastModified": "2026-03-26T06:14:22Z",
  "customProperties": {
    "airflow_dag_id": "orders_pipeline",
    "airflow_run_id": "scheduled__2026-03-26T05:00:00",
    "airflow_task_id": "load_orders",
    "row_count": "4821033",
    "data_as_of": "2026-03-26T05:59:44Z"
  }
}
```

```json
{
  "aspect": "upstreamLineage",
  "upstreams": [
    {"dataset": "urn:li:dataset:...:kafka:raw.orders", "type": "TRANSFORMED"},
    {"dataset": "urn:li:dataset:...:postgres:customers", "type": "TRANSFORMED"}
  ]
}
```

```json
{
  "aspect": "contractStatus",
  "status": "ACTIVE",
  "lastValidated": "2026-03-26T06:14:22Z",
  "violationCount": 0
}
```

This is the moment the entire organization — in DataHub's UI — sees the pipeline
ran successfully and honored its contract.

---

### 3. `ContractBreachGuardOperator`

The consumer-side operator. A consumer DAG uses this to gate its own execution on
whether its upstream dataset's contract is currently in a healthy state.

```python
from airflow.providers.data_contracts.operators.contract_breach_guard import ContractBreachGuardOperator

guard = ContractBreachGuardOperator(
    task_id="check_upstream_contracts",
    catalog_conn_id="datahub_default",
    dataset_urns=[
        "urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)",
        "urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.customers,PROD)",
    ],
    # What to do if upstream contract is BREACHED
    on_breach="fail",           # or: "skip", "warn"
    # Allow certain violation types to pass
    ignore_violations=["FRESHNESS"],   # consumer can tolerate slightly stale data
    # Break-glass override
    override_var="CONTRACT_GUARD_OVERRIDE",
    # Cache contract status to avoid hammering catalog on every task
    cache_ttl_seconds=300,
)
```

**The power of this operator:**

Today, when an upstream pipeline fails and produces bad data, the consumer DAG
has no idea. It runs, produces garbage downstream, and someone notices hours later.
`ContractBreachGuardOperator` makes the consumer DAG aware of its upstream's health
before it runs — the breach propagates through the dependency graph automatically,
because the catalog is the shared state that both producer and consumer read.

---

### 4. `SchemaEvolutionOperator`

Compares the actual schema of a dataset (post-transform) against the current
contract schema in the catalog, performs compatibility checking, and either
auto-approves non-breaking changes or blocks breaking ones pending review.

```python
from airflow.providers.data_contracts.operators.schema_evolution import SchemaEvolutionOperator

schema_check = SchemaEvolutionOperator(
    task_id="check_schema_evolution",
    catalog_conn_id="datahub_default",
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)",

    # Actual schema from the transform task
    actual_schema_xcom_task_id="transform_orders",
    actual_schema_xcom_key="output_schema",

    # Compatibility rules (from contract, but can be overridden)
    compatibility_mode="BACKWARD",   # BACKWARD | FORWARD | FULL | NONE

    # What to do with each change type
    on_breaking_change="fail",       # blocks the pipeline
    on_non_breaking_change="auto_approve",  # updates contract schema in catalog
    on_new_field="warn",             # new field: warn but allow

    # If a breaking change is detected, tag for review in catalog
    tag_for_review=True,
    reviewer="data-platform-oncall",
)
```

**Compatibility rules (aligned with Confluent Schema Registry):**

| Change | BACKWARD | FORWARD | FULL |
|---|---|---|---|
| Add optional field | OK | OK | OK |
| Remove field | BREAKING | OK | BREAKING |
| Rename field | BREAKING | BREAKING | BREAKING |
| Change type (widening) | OK | BREAKING | BREAKING |
| Change nullability (to nullable) | OK | OK | OK |
| Change nullability (to required) | BREAKING | BREAKING | BREAKING |

When a non-breaking change is auto-approved, the operator calls the catalog API
to update the contract's schema version — the catalog always reflects the current
actual schema.

---

### 5. `ContractReportOperator`

Generates a contract health report across all datasets owned by a team or tagged
with a label. Used in weekly governance review DAGs.

```python
from airflow.providers.data_contracts.operators.contract_report import ContractReportOperator

report = ContractReportOperator(
    task_id="weekly_contract_report",
    catalog_conn_id="datahub_default",
    # Filter by owner team, tag, or explicit list
    owner_team="data-platform",
    # or: tags=["critical", "pii"]
    # or: dataset_urns=[...]
    period_days=7,
    output_format="markdown",    # or: "json", "html"
    include_sections=[
        "summary",               # total contracts, breach rate
        "breaches",              # individual breach events with root cause
        "schema_changes",        # all schema evolutions this period
        "consumer_impact",       # which consumer DAGs were blocked
        "top_offenders",         # datasets with most breaches
    ],
    notify_hook_conn_id="slack_data_leads",
)
```

---

## Sensors

### `ContractReadySensor`

Waits until a dataset's contract is in ACTIVE status in the catalog — meaning the
producer DAG has successfully run and called `ContractPublishOperator`. This is the
replacement for polling a table or checking a file timestamp.

```python
from airflow.providers.data_contracts.sensors.contract_ready import ContractReadySensor

wait_for_orders = ContractReadySensor(
    task_id="wait_for_orders_contract",
    catalog_conn_id="datahub_default",
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)",
    # Wait until the dataset was updated after this point in time
    min_update_time="{{ data_interval_end }}",
    # Fail if contract is BREACHED rather than waiting
    fail_on_breach=True,
    timeout=7200,
    poke_interval=60,
)
```

This is arguably the most impactful sensor in the provider. Today, cross-DAG
dependencies rely on file sensors, table sensors, or time offsets. This sensor
makes the dependency semantic: "wait until the orders dataset has been published
and its contract satisfied" — not "wait until a file exists."

### `SchemaMatchSensor`

Waits until a dataset's schema in the catalog matches an expected schema version.
Used when a consumer DAG needs to wait for a producer to roll out a schema migration
before it can use the new fields.

```python
from airflow.providers.data_contracts.sensors.schema_match import SchemaMatchSensor

wait_for_schema = SchemaMatchSensor(
    task_id="wait_for_schema_v4",
    catalog_conn_id="datahub_default",
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)",
    min_schema_version=4,
    required_fields=["order_id", "customer_id", "discount_code"],  # new in v4
    timeout=3600,
    poke_interval=120,
)
```

---

## The DataHub Hook

The primary hook — implements the abstract `BaseCatalogHook` interface so other
catalogs can be swapped in without changing operator code.

```python
from airflow.providers.data_contracts.hooks.datahub import DataHubHook

hook = DataHubHook(conn_id="datahub_default")

# Fetch a contract
contract = hook.get_contract(
    dataset_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)"
)
# Returns: DataContract object

# Fetch contract by friendly name (resolves URN internally)
contract = hook.get_contract_by_name(
    platform="snowflake",
    name="prod.orders",
    env="PROD",
)

# Get contract status (lightweight — for sensors)
status = hook.get_contract_status(dataset_urn)
# Returns: "ACTIVE" | "BREACHED" | "DEPRECATED" | "DRAFT"

# Report a breach
hook.report_breach(
    dataset_urn=dataset_urn,
    violations=[violation1, violation2],
    dag_id="orders_pipeline",
    run_id=run_id,
)

# Update contract status
hook.update_contract_status(
    dataset_urn=dataset_urn,
    status="ACTIVE",
    last_validated_at=datetime.utcnow(),
    stats={"row_count": 4821033},
)

# Emit lineage
hook.emit_lineage(
    output_urn=dataset_urn,
    input_urns=upstream_urns,
    transformation_description="Airflow DAG: orders_pipeline",
)

# Get downstream consumers (for impact assessment)
consumers = hook.get_downstream_consumers(dataset_urn)
# Returns: list of dataset URNs + their owner teams

# Check schema compatibility
diff = hook.check_schema_compatibility(
    dataset_urn=dataset_urn,
    proposed_schema=actual_schema,
    compatibility_mode="BACKWARD",
)
# Returns: SchemaDiff with breaking_changes, new_fields, removed_fields
```

**Connection extras for DataHub:**
```json
{
  "gms_url": "http://datahub-gms:8080",
  "token": "your-datahub-personal-access-token",
  "env": "PROD",
  "timeout_sec": 30
}
```

### Abstract `BaseCatalogHook`

All catalog hooks implement this interface — operators use `BaseCatalogHook`
exclusively, making catalog choice a connection config decision, not a code change.

```python
from abc import ABC, abstractmethod

class BaseCatalogHook(ABC):

    @abstractmethod
    def get_contract(self, dataset_urn: str) -> DataContract: ...

    @abstractmethod
    def get_contract_status(self, dataset_urn: str) -> str: ...

    @abstractmethod
    def report_breach(self, dataset_urn: str, violations: list, **kwargs) -> None: ...

    @abstractmethod
    def update_contract_status(self, dataset_urn: str, status: str, **kwargs) -> None: ...

    @abstractmethod
    def emit_lineage(self, output_urn: str, input_urns: list[str], **kwargs) -> None: ...

    @abstractmethod
    def check_schema_compatibility(self, dataset_urn: str,
                                   proposed_schema: list, **kwargs) -> SchemaDiff: ...
```

---

## ContractResult — XCom envelope

```python
@dataclass
class ContractResult:
    dataset_urn: str
    dataset_name: str
    contract_version: int
    catalog_url: str             # deep link to the DataHub page

    # Validation outcome
    passed: bool
    violations: list[ContractViolation]
    critical_violations: list[ContractViolation]
    warning_violations: list[ContractViolation]

    # Stats validated
    actual_row_count: int | None
    actual_schema_version: int | None
    data_freshness_minutes: float | None
    run_duration_minutes: float | None

    # Schema diff (if schema changed)
    schema_diff: SchemaDiff | None

    # Breach reporting
    breach_reported_to_catalog: bool
    breach_id: str | None         # catalog-assigned breach event ID

    dag_id: str
    run_id: str
    task_id: str
    validated_at: datetime

    def raise_if_failed(self) -> None: ...
    def has_breaking_schema_change(self) -> bool: ...
    def consumer_impact_summary(self) -> str: ...

    @classmethod
    def from_xcom(cls, context: dict, task_id: str) -> "ContractResult": ...
```

---

## Contract Store

Local persistence in Airflow's metadata DB for breach history and contract version
caching. The catalog remains the source of truth — this is a local mirror for
performance and offline audit.

```sql
-- Contract version cache: avoid hitting catalog API on every task poke
CREATE TABLE dc_contract_cache (
    dataset_urn         TEXT PRIMARY KEY,
    contract_json       JSONB NOT NULL,
    contract_version    INTEGER NOT NULL,
    fetched_at          TIMESTAMP NOT NULL,
    ttl_seconds         INTEGER DEFAULT 300
);

-- Breach log: local audit trail of all violations
CREATE TABLE dc_breach_log (
    id                  SERIAL PRIMARY KEY,
    dataset_urn         TEXT NOT NULL,
    dag_id              VARCHAR(250) NOT NULL,
    run_id              VARCHAR(250) NOT NULL,
    task_id             VARCHAR(250) NOT NULL,
    violation_type      VARCHAR(50) NOT NULL,
    severity            VARCHAR(20) NOT NULL,
    expected            TEXT,
    actual              TEXT,
    field_name          TEXT,
    message             TEXT,
    reported_to_catalog BOOLEAN DEFAULT FALSE,
    catalog_breach_id   TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Consumer impact log: which consumer DAGs were blocked by a breach
CREATE TABLE dc_consumer_impact (
    id                  SERIAL PRIMARY KEY,
    producer_urn        TEXT NOT NULL,
    consumer_dag_id     VARCHAR(250) NOT NULL,
    consumer_run_id     VARCHAR(250),
    blocked_at          TIMESTAMP NOT NULL,
    unblocked_at        TIMESTAMP,
    block_reason        TEXT
);
```

---

## Example DAGs

### Pattern 1: Producer DAG — validate and publish contract

```python
from datetime import datetime
from airflow.decorators import dag, task
from airflow.providers.data_contracts.operators.contract_validate import ContractValidateOperator
from airflow.providers.data_contracts.operators.contract_publish import ContractPublishOperator
from airflow.providers.data_contracts.operators.schema_evolution import SchemaEvolutionOperator

ORDERS_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)"

@dag(schedule="0 5 * * *", start_date=datetime(2026, 1, 1), tags=["producer"])
def orders_producer_pipeline():

    @task
    def extract_from_kafka() -> dict:
        # ... consume from Kafka
        return {"rows": data, "schema": actual_schema}

    @task
    def transform(raw: dict) -> dict:
        # ... clean and enrich
        return {"rows": clean, "schema": output_schema}

    @task
    def load_to_snowflake(data: dict) -> dict:
        # ... write to Snowflake
        return {
            "row_count": len(data["rows"]),
            "schema": data["schema"],
            "data_as_of": datetime.utcnow().isoformat(),
        }

    # Check schema didn't break backward compatibility
    schema_check = SchemaEvolutionOperator(
        task_id="check_schema_evolution",
        catalog_conn_id="datahub_default",
        dataset_urn=ORDERS_URN,
        actual_schema_xcom_task_id="transform",
        actual_schema_xcom_key="schema",
        compatibility_mode="BACKWARD",
        on_breaking_change="fail",
        on_non_breaking_change="auto_approve",
    )

    # Validate the loaded data against the full contract
    validate = ContractValidateOperator(
        task_id="validate_contract",
        catalog_conn_id="datahub_default",
        dataset_urn=ORDERS_URN,
        stats_xcom_task_id="load_to_snowflake",
        stats_xcom_key="output_stats",
        validate_schema=True,
        validate_freshness=True,
        validate_completeness=True,
        on_schema_violation="fail",
        on_freshness_violation="warn",
        on_completeness_violation="fail",
        report_breach_to_catalog=True,
    )

    # Stamp the contract as ACTIVE in DataHub — consumers unblocked
    publish = ContractPublishOperator(
        task_id="publish_contract",
        catalog_conn_id="datahub_default",
        dataset_urn=ORDERS_URN,
        upstream_urns=[
            "urn:li:dataset:(urn:li:dataPlatform:kafka,raw.orders,PROD)",
        ],
        stats_xcom_task_id="load_to_snowflake",
        update_contract_status=True,
        emit_run_facet=True,
    )

    raw = extract_from_kafka()
    transformed = transform(raw)
    loaded = load_to_snowflake(transformed)

    loaded >> schema_check >> validate >> publish

orders_producer_pipeline()
```

### Pattern 2: Consumer DAG — wait for contract and guard on breach

```python
from airflow.providers.data_contracts.sensors.contract_ready import ContractReadySensor
from airflow.providers.data_contracts.operators.contract_breach_guard import ContractBreachGuardOperator

ORDERS_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.orders,PROD)"
CUSTOMERS_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,prod.customers,PROD)"

@dag(schedule="0 7 * * *", start_date=datetime(2026, 1, 1), tags=["consumer"])
def ml_feature_pipeline():

    # Wait until upstream datasets have honored their contracts today
    wait_orders = ContractReadySensor(
        task_id="wait_for_orders",
        catalog_conn_id="datahub_default",
        dataset_urn=ORDERS_URN,
        min_update_time="{{ data_interval_end }}",
        fail_on_breach=True,     # don't wait if it's breached — fail fast
        timeout=7200,
    )

    wait_customers = ContractReadySensor(
        task_id="wait_for_customers",
        catalog_conn_id="datahub_default",
        dataset_urn=CUSTOMERS_URN,
        min_update_time="{{ data_interval_end }}",
        fail_on_breach=False,    # customers data: tolerate breach, just warn
        timeout=3600,
    )

    # Final guard before expensive feature computation
    guard = ContractBreachGuardOperator(
        task_id="guard_on_upstream_health",
        catalog_conn_id="datahub_default",
        dataset_urns=[ORDERS_URN, CUSTOMERS_URN],
        on_breach="fail",
        ignore_violations=["FRESHNESS"],    # ML can tolerate slightly stale data
    )

    @task
    def compute_features(): ...

    @task
    def train_model(features): ...

    [wait_orders, wait_customers] >> guard
    features = compute_features()
    guard >> features >> train_model(features)

ml_feature_pipeline()
```

### Pattern 3: Schema migration with consumer awareness

```python
@dag(schedule=None, start_date=datetime(2026, 1, 1), tags=["schema-migration"])
def orders_schema_v4_migration():
    """
    Adds 'discount_code' field to orders table.
    Waits until all consumer DAGs have upgraded to expect the new field.
    """

    @task
    def apply_migration():
        # ALTER TABLE prod.orders ADD COLUMN discount_code VARCHAR(50)
        return {"new_schema_version": 4}

    schema_check = SchemaEvolutionOperator(
        task_id="validate_backward_compat",
        catalog_conn_id="datahub_default",
        dataset_urn=ORDERS_URN,
        actual_schema_xcom_task_id="apply_migration",
        actual_schema_xcom_key="new_schema_version",
        compatibility_mode="BACKWARD",    # adding nullable field = backward compatible
        on_breaking_change="fail",
        on_non_breaking_change="auto_approve",
    )

    publish = ContractPublishOperator(
        task_id="publish_new_schema",
        catalog_conn_id="datahub_default",
        dataset_urn=ORDERS_URN,
        update_contract_status=True,
    )

    apply_migration() >> schema_check >> publish
```

---

## UI Extension — Contract Health Panel

An Airflow plugin adds a **Contracts** tab showing:

**Contract status board** — one row per registered dataset contract. Columns:
dataset name, owner team, current status (ACTIVE / BREACHED / DEPRECATED), last
validated, breach count (30 days), schema version, and a deep link to DataHub.
Color-coded: green = ACTIVE, red = BREACHED, gray = DEPRECATED.

**Breach timeline** — time-series chart of breach events across all contracts,
grouped by violation type (schema, freshness, completeness, SLA). Shows whether
breach frequency is trending up or down.

**Consumer impact view** — for each breach, which consumer DAGs were blocked
and for how long. Quantifies the downstream blast radius of each producer failure.

**Schema evolution log** — chronological list of schema changes, with compatibility
assessment, auto-approve vs manual review status, and linked DAG run.

---

## CLI Extension

```bash
# Show health of all registered contracts
airflow contracts status

# Show contract detail for a dataset (fetches live from catalog)
airflow contracts show --urn "urn:li:dataset:..."
airflow contracts show --name "prod.orders" --platform snowflake

# List recent breaches
airflow contracts breaches --days 7

# List consumer DAGs blocked by a breach
airflow contracts impact --urn "urn:li:dataset:..."

# Manually resolve a breach (marks ACTIVE in catalog)
airflow contracts resolve --urn "urn:li:dataset:..." --reason "Re-run completed"

# Validate a contract definition YAML before registering it
airflow contracts validate slos/orders_contract.yaml

# Show schema diff between contract version and actual
airflow contracts schema-diff \
  --urn "urn:li:dataset:..." \
  --conn-id snowflake_default \
  --table prod.orders
```

---

## Supported Catalogs

| Catalog | Hook class | API used |
|---|---|---|
| DataHub | `DataHubHook` | GMS REST API + DataHub Python SDK |
| OpenMetadata | `OpenMetadataHook` | OpenMetadata REST API |
| Atlan | `AtlanHook` | Atlan Python SDK |
| Collibra | `CollibraHook` | Collibra REST API |
| Alation | `AlationHook` | Alation REST API |
| Custom | Extend `BaseCatalogHook` | Any REST API or Python SDK |

For teams without a catalog, the provider also supports loading contracts from
local YAML files committed alongside DAGs — a "catalog-lite" mode that gives the
enforcement benefits without requiring catalog infrastructure.

```yaml
# contracts/prod.orders.yaml  (catalog-lite mode)
dataset_urn: "local:prod.orders"
dataset_name: "prod.orders"
version: 3
schema:
  - name: order_id
    type: STRING
    nullable: false
    primary_key: true
  - name: customer_id
    type: STRING
    nullable: false
  - name: total_amount
    type: FLOAT
    nullable: false
  - name: created_at
    type: TIMESTAMP
    nullable: false
freshness_max_age_minutes: 90
min_row_count: 100000
sla_completion_minutes: 45
producer_team: data-platform
consumer_teams: [ml-team, finance]
```

---

## Backwards Compatibility

Fully additive new provider. No changes to Airflow core.

Catalog-lite mode (YAML contracts) requires zero external dependencies — teams can
adopt the provider without a catalog and migrate to DataHub/OpenMetadata later. The
operator interface is identical in both modes; only the `catalog_conn_id` changes.

---

## Implementation Plan

### Phase 1 — DataHub core (v1.0)
- `DataHubHook` with get_contract, report_breach, update_status, emit_lineage
- `ContractValidateOperator` (schema + completeness validation)
- `ContractPublishOperator`
- `ContractBreachGuardOperator`
- `ContractReadySensor`
- `DataContract` + `ContractResult` models
- Breach log schema + migrations
- Catalog-lite mode (YAML contracts)
- Unit tests with mocked DataHub responses

### Phase 2 — Schema evolution + sensors (v1.1)
- `SchemaEvolutionOperator` with full compatibility matrix
- `SchemaMatchSensor`
- Freshness + SLA validators in `ContractValidateOperator`
- `OpenMetadataHook`, `AtlanHook`
- Consumer impact tracking

### Phase 3 — UI + reporting (v1.2)
- Contract Health UI plugin
- `ContractReportOperator`
- `airflow contracts` CLI
- `CollibraHook`, `AlationHook`

### Phase 4 — Ecosystem (v2.0)
- OpenLineage integration (emit OpenLineage events alongside catalog updates)
- Integration with `apache-airflow-provider-pipeline-slo`
  (SLO breach → contract breach propagation)
- Contract versioning workflow (propose → review → approve → publish)
- Multi-environment contract promotion (dev → staging → prod)

---

## References

- DataHub GMS REST API: https://datahubproject.io/docs/api/restli/
- DataHub Python SDK: https://datahubproject.io/docs/metadata-ingestion/
- OpenMetadata API: https://docs.open-metadata.org/api
- Data Contract Specification (open standard): https://datacontract.com/
- Confluent Schema Registry compatibility: https://docs.confluent.io/platform/current/schema-registry/fundamentals/schema-evolution.html
- Pact (contract testing for APIs): https://pact.io/
- Billy Bosworth's "Data Contracts" talk, Data Council 2023
