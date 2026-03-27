# AIP-YY: Real-Time / Streaming Bridge Provider

**Author:** [Your Name]  
**Status:** Draft  
**Created:** 2026-03-26  
**Category:** Provider  
**Discussions-To:** https://github.com/apache/airflow/discussions

---

## Abstract

This AIP proposes `apache-airflow-provider-streaming-bridge` — a provider that unifies batch and streaming workloads in a single DAG without turning Airflow into a stream processor. Airflow remains the conductor: it owns offsets, manages checkpoints, triggers streaming jobs, waits on stream conditions, and handles backfill — while Kafka, Flink, Spark Structured Streaming, and Kinesis do the actual stream processing. One DAG. One scheduler. One place to look when things break.

---

## Motivation

### The status quo is two separate worlds

Every modern data platform runs both batch and streaming pipelines. Batch jobs in Airflow. Streaming jobs in Kafka + Flink or Spark Streaming. The two worlds are stitched together with:

- Shell scripts that poll Kafka consumer lag and fire Airflow DAGs
- Cron + sleep loops that wait for Flink checkpoints
- Manual offset tracking in Redis or Postgres
- Separate alerting stacks for each world
- Two on-call rotations that don't understand each other's systems

This is the 2018 data quality problem all over again — solved ad-hoc by every team, standardized by nobody.

### What Airflow should NOT do

Airflow should not become a stream processor. It has no continuous execution model, no event time semantics, no watermark propagation. Flink and Spark Structured Streaming do those things well.

### What Airflow SHOULD do

Airflow is already the scheduler, the dependency graph, the retry engine, the alerting layer, and the audit log. It should own:

- **Offset management** — track where each DAG run left off in a topic
- **Checkpoint coordination** — wait for a streaming job's checkpoint before considering a batch window closed
- **Backfill from streams** — replay a topic offset range when a batch job fails
- **Window triggering** — fire batch tasks when a stream window closes
- **Health gating** — block downstream batch tasks if consumer lag is unhealthy
- **Cross-world lineage** — a single DAG graph that shows both batch tasks and stream boundaries

### The analogy

| | Before | After this provider |
|---|---|---|
| Data quality | Custom assert scripts | Great Expectations checkpoints |
| Stream + batch | Two separate systems, glue code | One DAG, stream-aware operators |
| Offset tracking | Redis / manual Postgres table | Airflow metadata DB, first-class |
| Backfill | Re-run Flink job manually | `StreamBackfillOperator` in the DAG |
| Lag alerting | Separate Datadog dashboards | Airflow SLA alerts, same UI |

---

## Goals

1. Provide Operators that interact with streaming systems as first-class DAG tasks
2. Provide Sensors that wait on streaming conditions before batch proceeds
3. Provide Hooks for Kafka, Kinesis, Pulsar, Flink, and Spark Streaming
4. Provide a Checkpoint Store in Airflow's metadata DB — no new external dependency
5. Provide a `StreamContext` XCom object that carries offset/watermark/window state between tasks
6. Extend the Airflow UI with a Stream Health panel
7. Expose `airflow streams` CLI commands

## Non-Goals

- Running stream processing logic inside Airflow workers (Flink/Spark do this)
- Replacing Kafka, Flink, Kinesis, or Spark
- Sub-second latency (Airflow's scheduler is not built for this; minimum meaningful window is ~30s)
- Stateful stream joins inside Airflow tasks

---

## Core Design Principle: Airflow as Conductor

```
┌─────────────────────────────────────────────────────────┐
│                    Apache Airflow                       │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ Sensors  │  │  Operators   │  │ Checkpoint Store │  │
│  │ (wait on │  │ (trigger,    │  │ (offsets, window │  │
│  │  stream) │  │  backfill,   │  │  state, lag log) │  │
│  └────┬─────┘  │  publish)    │  └──────────────────┘  │
│       │        └──────┬───────┘                        │
└───────┼───────────────┼────────────────────────────────┘
        │               │
   ┌────▼───────────────▼────┐
   │   Streaming systems      │
   │  Kafka · Flink · Spark  │
   │  Kinesis · Pulsar       │
   └─────────────────────────┘
```

Airflow tasks call streaming systems via hooks. They never run stream processing code themselves. A `StreamCheckpointOperator` doesn't process events — it tells Flink to take a savepoint and records the result. A `StreamBackfillOperator` doesn't replay events — it instructs Spark to consume a specific offset range and waits for completion.

---

## Proposed Design

### Package structure

```
apache-airflow-provider-streaming-bridge/
├── airflow/
│   └── providers/
│       └── streaming_bridge/
│           ├── __init__.py
│           ├── operators/
│           │   ├── stream_trigger.py
│           │   ├── stream_checkpoint.py
│           │   ├── stream_backfill.py
│           │   ├── window_aggregate.py
│           │   └── stream_publish.py
│           ├── sensors/
│           │   ├── topic_lag.py
│           │   ├── checkpoint_ready.py
│           │   ├── stream_healthy.py
│           │   └── window_closed.py
│           ├── hooks/
│           │   ├── kafka_stream.py
│           │   ├── kinesis_stream.py
│           │   ├── pulsar_stream.py
│           │   ├── flink_bridge.py
│           │   └── spark_stream.py
│           ├── store/
│           │   ├── offset_registry.py
│           │   ├── window_state.py
│           │   └── lag_telemetry.py
│           ├── models/
│           │   └── stream_context.py
│           └── ui/
│               └── stream_health_plugin.py
├── tests/
└── provider.yaml
```

---

## Operators

### 1. `StreamTriggerOperator`

Triggers a streaming job (Flink, Spark, Beam) for a specific offset range and waits for it to reach the end of that range. Turns a streaming job into a bounded, batch-like task in the DAG.

```python
from airflow.providers.streaming_bridge.operators.stream_trigger import StreamTriggerOperator

trigger = StreamTriggerOperator(
    task_id="run_order_stream",
    hook_conn_id="flink_default",
    job_name="order_aggregation",
    topic="orders",
    # Read from last committed offset to current watermark
    from_offset="checkpoint",          # or: specific offset, "earliest", "latest"
    to_offset="watermark",             # or: specific offset, timestamp
    timeout=1800,                      # fail if streaming job exceeds 30min
    push_context_to_xcom=True,         # downstream tasks receive StreamContext
)
```

**Execution model:**
1. Reads committed offset for `(dag_id, run_id, topic)` from Checkpoint Store
2. Queries current watermark from the streaming system
3. Submits the bounded job to Flink/Spark with `[from_offset, to_offset]`
4. Polls for job completion (via `FlinkBridgeHook.get_job_status`)
5. On success: commits the new offset to Checkpoint Store, pushes `StreamContext` to XCom
6. On failure: leaves offset uncommitted so next run retries from the same point

---

### 2. `StreamCheckpointOperator`

Instructs a streaming job to take a checkpoint/savepoint at the current position. Used to create a recovery point before a risky downstream batch operation.

```python
from airflow.providers.streaming_bridge.operators.stream_checkpoint import StreamCheckpointOperator

checkpoint = StreamCheckpointOperator(
    task_id="checkpoint_before_load",
    hook_conn_id="flink_default",
    job_name="order_aggregation",
    savepoint_dir="s3://my-bucket/flink-savepoints/",
    wait_for_completion=True,
    push_context_to_xcom=True,
)
```

**What gets stored in Checkpoint Store:**
- Savepoint path
- Offset at checkpoint time
- Watermark at checkpoint time
- DAG run ID, task ID, timestamp
- Job name and streaming system identifier

Downstream tasks receive a `StreamContext` containing the savepoint path — enabling restore if the batch job fails and a human needs to roll back.

---

### 3. `StreamBackfillOperator`

Replays a specific range of a topic through a streaming job. The critical operator for the "batch pipeline failed, re-process the events it missed" scenario that today requires manual Flink/Spark intervention.

```python
from airflow.providers.streaming_bridge.operators.stream_backfill import StreamBackfillOperator

backfill = StreamBackfillOperator(
    task_id="backfill_missed_orders",
    hook_conn_id="flink_default",
    job_name="order_aggregation",
    topic="orders",
    from_offset="{{ ti.xcom_pull('last_good_checkpoint')['offset'] }}",
    to_offset="{{ ti.xcom_pull('current_watermark')['offset'] }}",
    # or use time-based bounds:
    from_timestamp="{{ data_interval_start }}",
    to_timestamp="{{ data_interval_end }}",
    output_topic="orders_backfilled",
    timeout=3600,
    on_duplicate="skip",     # or: "overwrite", "fail"
)
```

**Deduplication strategy:**
- `skip` — compare output record keys against the target store, skip existing
- `overwrite` — re-write regardless (idempotent pipelines only)
- `fail` — raise on first duplicate detection

---

### 4. `WindowAggregateOperator`

Waits for a time window to close in a streaming system, then fetches the pre-computed aggregates into the batch world (XCom or a target store). Bridges stream-computed metrics into batch DAG tasks without re-computing them.

```python
from airflow.providers.streaming_bridge.operators.window_aggregate import WindowAggregateOperator

fetch_window = WindowAggregateOperator(
    task_id="fetch_hourly_order_counts",
    hook_conn_id="kafka_default",
    topic="order_counts_hourly",         # Flink writes windowed results here
    window_start="{{ data_interval_start }}",
    window_end="{{ data_interval_end }}",
    key_field="window_end",
    value_fields=["order_count", "revenue", "avg_basket"],
    output_format="dict",                # or: "dataframe", "json"
    result_xcom_key="hourly_counts",
)
```

**Flow:**
1. Sensor checks that window results are present in the output topic
2. Operator consumes exactly the records in `[window_start, window_end]`
3. Returns structured aggregates via XCom
4. Downstream batch task uses them like any other task output

---

### 5. `StreamPublishOperator`

Publishes records from a batch task (XCom, file, query result) into a streaming topic. The outbound direction — batch results flowing into the stream world.

```python
from airflow.providers.streaming_bridge.operators.stream_publish import StreamPublishOperator

publish = StreamPublishOperator(
    task_id="publish_enriched_orders",
    hook_conn_id="kafka_default",
    topic="orders_enriched",
    source_xcom_task_id="enrich_orders",
    key_field="order_id",
    serializer="json",                  # or: "avro", "protobuf"
    schema_registry_conn_id="schema_registry_default",
    compression="snappy",
    partition_strategy="key_hash",      # or: "round_robin", "custom_fn"
    exactly_once=True,                  # uses Kafka transactions
)
```

---

## Sensors

### `TopicLagSensor`

Blocks the DAG until consumer lag for a topic/consumer group drops below a threshold. The most commonly needed stream condition in batch orchestration.

```python
from airflow.providers.streaming_bridge.sensors.topic_lag import TopicLagSensor

wait_for_catchup = TopicLagSensor(
    task_id="wait_for_low_lag",
    hook_conn_id="kafka_default",
    topic="orders",
    consumer_group="order_aggregation",
    max_lag=1000,               # messages
    timeout=3600,
    poke_interval=30,
    soft_fail=True,             # warn instead of fail if timeout hit
)
```

### `CheckpointReadySensor`

Waits until a streaming job has committed a checkpoint at or past a specific offset or timestamp. Used to ensure a streaming job has "processed up to this point" before a batch window starts.

```python
from airflow.providers.streaming_bridge.sensors.checkpoint_ready import CheckpointReadySensor

wait_for_checkpoint = CheckpointReadySensor(
    task_id="wait_for_stream_checkpoint",
    hook_conn_id="flink_default",
    job_name="order_aggregation",
    min_watermark="{{ data_interval_end }}",
    timeout=1800,
    poke_interval=60,
)
```

### `StreamHealthySensor`

Validates that a streaming job is running, not in a degraded state, and its consumer lag is within bounds — before allowing downstream batch tasks to proceed.

```python
from airflow.providers.streaming_bridge.sensors.stream_healthy import StreamHealthySensor

health_check = StreamHealthySensor(
    task_id="check_stream_health",
    hook_conn_id="flink_default",
    job_name="order_aggregation",
    checks=["job_running", "lag_healthy", "no_restarts_last_hour"],
    timeout=300,
)
```

### `WindowClosedSensor`

Waits until a tumbling or sliding window has closed in a streaming system and results are available downstream (typically in an output topic or state store).

```python
from airflow.providers.streaming_bridge.sensors.window_closed import WindowClosedSensor

wait_for_window = WindowClosedSensor(
    task_id="wait_for_hourly_window",
    hook_conn_id="kafka_default",
    output_topic="order_counts_hourly",
    window_end="{{ data_interval_end }}",
    key_field="window_end",
    timeout=1800,
    poke_interval=30,
)
```

---

## Hooks

### `KafkaStreamHook`

Core hook for all Kafka interactions. Wraps the Confluent Kafka Python client with Airflow connection management.

```python
from airflow.providers.streaming_bridge.hooks.kafka_stream import KafkaStreamHook

hook = KafkaStreamHook(conn_id="kafka_default")

# Offset operations
offsets = hook.get_committed_offsets(topic="orders", consumer_group="my_group")
watermark = hook.get_high_watermark(topic="orders")
lag = hook.get_consumer_lag(topic="orders", consumer_group="my_group")

# Consume a bounded range
records = hook.consume_range(
    topic="orders",
    from_offset=1000,
    to_offset=2000,
    timeout=60,
)

# Publish with transactions
with hook.transaction() as txn:
    txn.produce(topic="orders_enriched", key="order_1", value=payload)
```

**Connection extras:**
```json
{
  "bootstrap_servers": "kafka-1:9092,kafka-2:9092",
  "security_protocol": "SASL_SSL",
  "sasl_mechanism": "SCRAM-SHA-512",
  "schema_registry_url": "http://schema-registry:8081"
}
```

### `FlinkBridgeHook`

Interacts with the Flink REST API to submit jobs, query job status, trigger savepoints, and retrieve checkpoint metadata.

```python
from airflow.providers.streaming_bridge.hooks.flink_bridge import FlinkBridgeHook

hook = FlinkBridgeHook(conn_id="flink_default")

job_id = hook.submit_job(
    jar_id="order_aggregation.jar",
    entry_class="com.example.OrderAggregation",
    program_args=["--from-offset", "1000", "--to-offset", "2000"],
    parallelism=4,
)

status = hook.get_job_status(job_id)          # RUNNING, FINISHED, FAILED, CANCELED

savepoint_path = hook.trigger_savepoint(
    job_id=job_id,
    target_dir="s3://bucket/savepoints/",
    cancel_job=False,
)

metrics = hook.get_job_metrics(job_id)        # checkpoints, restarts, throughput
```

### `SparkStreamHook`

Submits and monitors Spark Structured Streaming jobs via Spark Connect or the Spark REST API. Supports both streaming and bounded (batch replay) modes.

```python
from airflow.providers.streaming_bridge.hooks.spark_stream import SparkStreamHook

hook = SparkStreamHook(conn_id="spark_default")

app_id = hook.submit_streaming_job(
    script="s3://scripts/order_aggregation.py",
    args={"from_offset": 1000, "to_offset": 2000, "mode": "bounded"},
    conf={"spark.streaming.kafka.maxRatePerPartition": "1000"},
)

status = hook.wait_for_completion(app_id, timeout=3600)
checkpoint_location = hook.get_checkpoint_location(app_id)
```

### `KinesisStreamHook`

Wraps boto3 Kinesis client with shard-aware offset tracking (using sequence numbers), exactly-once consumption patterns, and enhanced fan-out support.

### `PulsarStreamHook`

Wraps the Pulsar Python client with message ID-based offset tracking and support for Pulsar Functions as stream processing units.

---

## StreamContext — XCom envelope

All streaming operators push a typed `StreamContext` to XCom, carrying the full state of the stream boundary so downstream tasks have everything they need without re-querying the streaming system.

```python
@dataclass
class StreamContext:
    topic: str
    consumer_group: str | None
    from_offset: int | str           # partition -> offset dict for multi-partition
    to_offset: int | str
    watermark: datetime
    window_start: datetime | None
    window_end: datetime | None
    lag_at_start: int | None
    lag_at_end: int | None
    checkpoint_path: str | None      # Flink savepoint / Spark checkpoint location
    records_processed: int | None
    streaming_system: str            # "kafka", "kinesis", "pulsar"
    job_id: str | None               # Flink job ID / Spark app ID
    dag_id: str
    run_id: str
    task_id: str
    committed_at: datetime

    def to_offset_args(self) -> dict: ...
    def assert_window_closed(self) -> None: ...
    def raise_if_lag_exceeded(self, max_lag: int) -> None: ...

    @classmethod
    def from_xcom(cls, context: dict, task_id: str) -> "StreamContext": ...
```

Downstream tasks branch on stream state:

```python
from airflow.providers.streaming_bridge.models.stream_context import StreamContext

def decide_backfill(**context):
    sc = StreamContext.from_xcom(context, task_id="run_order_stream")
    if sc.lag_at_end > 5000:
        return "trigger_backfill"
    return "proceed_to_load"
```

---

## Checkpoint Store

A lightweight persistence layer in Airflow's metadata DB. No new external dependencies.

### Schema additions

```sql
-- Offset registry: tracks where each dag+topic left off
CREATE TABLE stream_offsets (
    id              SERIAL PRIMARY KEY,
    dag_id          VARCHAR(250) NOT NULL,
    run_id          VARCHAR(250) NOT NULL,
    task_id         VARCHAR(250) NOT NULL,
    topic           VARCHAR(500) NOT NULL,
    consumer_group  VARCHAR(500),
    streaming_system VARCHAR(50) NOT NULL,   -- kafka, kinesis, pulsar
    offsets         JSONB NOT NULL,          -- partition -> offset map
    watermark       TIMESTAMP,
    committed_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(dag_id, topic, consumer_group)    -- last-writer-wins per topic per DAG
);

-- Window state: closed windows and their result locations
CREATE TABLE stream_windows (
    id              SERIAL PRIMARY KEY,
    dag_id          VARCHAR(250) NOT NULL,
    run_id          VARCHAR(250) NOT NULL,
    topic           VARCHAR(500) NOT NULL,
    window_start    TIMESTAMP NOT NULL,
    window_end      TIMESTAMP NOT NULL,
    result_topic    VARCHAR(500),
    result_offset   JSONB,
    status          VARCHAR(20) NOT NULL,    -- open, closed, consumed
    closed_at       TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Lag telemetry: time-series consumer lag for alerting and UI
CREATE TABLE stream_lag_log (
    id              SERIAL PRIMARY KEY,
    dag_id          VARCHAR(250),
    topic           VARCHAR(500) NOT NULL,
    consumer_group  VARCHAR(500) NOT NULL,
    lag             BIGINT NOT NULL,
    partition_lags  JSONB,
    recorded_at     TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Checkpoint registry: savepoints and recovery points
CREATE TABLE stream_checkpoints (
    id              SERIAL PRIMARY KEY,
    dag_id          VARCHAR(250) NOT NULL,
    run_id          VARCHAR(250) NOT NULL,
    task_id         VARCHAR(250) NOT NULL,
    job_name        VARCHAR(500),
    streaming_system VARCHAR(50),
    checkpoint_path TEXT,
    offset_at_checkpoint JSONB,
    watermark_at_checkpoint TIMESTAMP,
    created_at      TIMESTAMP NOT NULL DEFAULT NOW()
);
```

---

## UI Extension

An Airflow plugin adds a **Streams** tab to the navigation with:

- **Lag dashboard** — per-topic consumer lag over time (line chart, last 24h). Red band at threshold.
- **Offset tracker** — table of all DAG + topic pairs, showing last committed offset vs current high watermark (gap = unprocessed messages)
- **Window status** — open / closed / consumed windows per topic, with time since close
- **Checkpoint log** — list of savepoints with path, offset, and linked DAG run. One-click restore instructions.
- **Cross-world DAG view** — augments the existing DAG graph to show stream boundaries as a distinct node shape (wave icon), making it visually clear where the batch graph hands off to the stream world and back

---

## CLI Extension

```bash
# Show lag for all topics watched by Airflow DAGs
airflow streams lag

# Show lag for a specific topic
airflow streams lag --topic orders --consumer-group order_aggregation

# Show committed offsets for a DAG
airflow streams offsets --dag-id my_hybrid_pipeline

# Manually commit an offset (for recovery)
airflow streams commit --dag-id my_hybrid_pipeline --topic orders --offset 42000

# List checkpoints for a DAG
airflow streams checkpoints --dag-id my_hybrid_pipeline

# Show open windows
airflow streams windows --topic order_counts_hourly --status open

# Replay a topic range (triggers backfill DAG run)
airflow streams backfill --dag-id my_hybrid_pipeline \
  --topic orders \
  --from 2026-03-25T00:00:00 \
  --to   2026-03-26T00:00:00
```

---

## Example DAG: Hybrid batch + streaming pipeline

A real order pipeline: streaming aggregation runs continuously in Flink, batch jobs run hourly to load results into a warehouse and send a report.

```python
from datetime import datetime
from airflow.decorators import dag, task
from airflow.providers.streaming_bridge.sensors.stream_healthy import StreamHealthySensor
from airflow.providers.streaming_bridge.sensors.window_closed import WindowClosedSensor
from airflow.providers.streaming_bridge.operators.stream_checkpoint import StreamCheckpointOperator
from airflow.providers.streaming_bridge.operators.window_aggregate import WindowAggregateOperator
from airflow.providers.streaming_bridge.operators.stream_publish import StreamPublishOperator
from airflow.providers.streaming_bridge.operators.stream_backfill import StreamBackfillOperator
from airflow.providers.streaming_bridge.models.stream_context import StreamContext

@dag(
    schedule="@hourly",
    start_date=datetime(2026, 1, 1),
    catchup=True,                     # important: backfill works for past windows too
)
def hybrid_order_pipeline():

    # 1. Gate: don't proceed if Flink is unhealthy
    health = StreamHealthySensor(
        task_id="check_flink_health",
        hook_conn_id="flink_default",
        job_name="order_aggregation",
        checks=["job_running", "lag_healthy"],
        timeout=300,
    )

    # 2. Wait for Flink to close the hourly window and write results to Kafka
    window_ready = WindowClosedSensor(
        task_id="wait_for_hourly_window",
        hook_conn_id="kafka_default",
        output_topic="order_counts_hourly",
        window_end="{{ data_interval_end }}",
        key_field="window_end",
        timeout=1800,
        poke_interval=30,
    )

    # 3. Checkpoint Flink before we do anything destructive in the batch world
    checkpoint = StreamCheckpointOperator(
        task_id="checkpoint_flink",
        hook_conn_id="flink_default",
        job_name="order_aggregation",
        savepoint_dir="s3://my-bucket/savepoints/",
        wait_for_completion=True,
    )

    # 4. Fetch the pre-computed window aggregates into the DAG
    fetch_counts = WindowAggregateOperator(
        task_id="fetch_hourly_counts",
        hook_conn_id="kafka_default",
        topic="order_counts_hourly",
        window_start="{{ data_interval_start }}",
        window_end="{{ data_interval_end }}",
        value_fields=["order_count", "revenue", "avg_basket", "region"],
        result_xcom_key="hourly_counts",
    )

    # 5. Standard batch tasks — completely unchanged, use XCom normally
    @task
    def enrich_with_crm(hourly_counts):
        # Join with Salesforce CRM data
        return enriched_counts

    @task
    def load_to_warehouse(enriched):
        # Write to Snowflake
        pass

    @task
    def send_finance_report(enriched):
        # Email CFO
        pass

    # 6. Publish enriched results back into Kafka for downstream consumers
    publish = StreamPublishOperator(
        task_id="publish_enriched_counts",
        hook_conn_id="kafka_default",
        topic="order_counts_enriched",
        source_xcom_task_id="enrich_with_crm",
        key_field="region",
        serializer="json",
        exactly_once=True,
    )

    # Wire the graph
    counts = fetch_counts.output
    enriched = enrich_with_crm(counts)

    health >> window_ready >> checkpoint >> fetch_counts
    enriched >> [load_to_warehouse(enriched), send_finance_report(enriched), publish]


@dag(
    schedule=None,                    # triggered manually or by CLI backfill command
    start_date=datetime(2026, 1, 1),
)
def order_backfill_pipeline():
    """
    Separate DAG for replaying missed events when the main pipeline failed.
    Triggered by: airflow streams backfill --dag-id order_backfill_pipeline
    """

    backfill = StreamBackfillOperator(
        task_id="backfill_orders",
        hook_conn_id="flink_default",
        job_name="order_aggregation",
        topic="orders",
        from_timestamp="{{ dag_run.conf['from'] }}",
        to_timestamp="{{ dag_run.conf['to'] }}",
        output_topic="order_counts_hourly",
        on_duplicate="overwrite",
        timeout=7200,
    )

    @task
    def validate_backfill_output(stream_context: dict):
        sc = StreamContext(**stream_context)
        sc.raise_if_lag_exceeded(max_lag=0)  # must reach zero lag
        return sc.records_processed

    validate_backfill_output(backfill.output)


hybrid_order_pipeline()
order_backfill_pipeline()
```

---

## Connections

| Conn ID | Conn Type | Used By |
|---|---|---|
| `kafka_default` | HTTP (custom) | `KafkaStreamHook` |
| `flink_default` | HTTP | `FlinkBridgeHook` (REST API) |
| `spark_default` | HTTP | `SparkStreamHook` (Spark Connect / REST) |
| `kinesis_default` | AWS | `KinesisStreamHook` (boto3) |
| `pulsar_default` | HTTP (custom) | `PulsarStreamHook` |
| `schema_registry_default` | HTTP | Schema validation in `StreamPublishOperator` |

New connection type: `kafka` — registered via the provider's `connection-types` in `provider.yaml`.

---

## Key Design Decisions

### 1. Offsets committed only on success

The Checkpoint Store uses a "commit on success only" model identical to Kafka's own consumer group commit semantics. If a DAG run fails, the offset is not advanced — the next run picks up from where the last successful run ended. This gives exactly-once semantics at the DAG level without requiring idempotent downstream systems.

### 2. Partition-aware offset tracking

Offsets are stored as JSONB partition maps `{"0": 1000, "1": 2050, "2": 998}` not a single integer. This correctly handles topic repartitioning and allows per-partition lag computation.

### 3. Catchup = streaming backfill

When `catchup=True` and the DAG has been paused, each historical DAG run fires a bounded stream job for its window. The Checkpoint Store ensures consecutive runs consume consecutive offset ranges — no gaps, no overlaps. This is the key insight that makes streaming feel native to Airflow's scheduling model.

### 4. No new scheduler changes

All polling is done in Sensor `poke()` calls. Streaming events do not push into Airflow's scheduler — Airflow still pulls. This is intentional: it keeps the provider additive and avoids scheduler modifications.

---

## Backwards Compatibility

This is a new provider package. Fully additive. No changes to Airflow core.

Schema additions are applied via Alembic migrations only when the provider is installed.

Existing Kafka / Kinesis / Spark providers (`apache-airflow-provider-apache-kafka`, `apache-airflow-provider-amazon`) are not replaced — this provider complements them with streaming-specific orchestration primitives. Users of existing providers can adopt this incrementally.

---

## Implementation Plan

### Phase 1 — Kafka + Flink core (v1.0)
- `KafkaStreamHook` with offset tracking, lag query, bounded consume
- `FlinkBridgeHook` with job submit, status poll, savepoint trigger
- `TopicLagSensor`, `CheckpointReadySensor`
- `StreamCheckpointOperator`, `WindowAggregateOperator`
- `StreamContext` XCom model
- Checkpoint Store schema + migrations
- Full test suite with mocked Kafka (Testcontainers)

### Phase 2 — Kinesis + Spark + backfill (v1.1)
- `KinesisStreamHook`, `SparkStreamHook`
- `StreamBackfillOperator`, `StreamTriggerOperator`
- `WindowClosedSensor`, `StreamHealthySensor`
- `StreamPublishOperator` with exactly-once (Kafka transactions)

### Phase 3 — UI + CLI (v1.2)
- Stream Health UI plugin (lag dashboard, offset tracker, window status)
- `airflow streams` CLI commands
- Cross-world DAG graph rendering

### Phase 4 — Pulsar + ecosystem (v2.0)
- `PulsarStreamHook`
- Integration with OpenLineage (stream boundaries as lineage nodes)
- Integration with `apache-airflow-provider-ai-governance` (govern LLM output streams)
- Support for Kafka Streams and ksqlDB as streaming systems

---

## Alternatives Considered

**Use Airflow's existing `KafkaConsumerOperator`:** The existing operator reads messages in a task but has no offset persistence, no window awareness, no backfill model, and no streaming system coordination. It treats Kafka as a data source, not a streaming system. This provider treats it as a parallel world that Airflow orchestrates.

**Run Flink/Spark jobs via `BashOperator` or `KubernetesPodOperator`:** Works for fire-and-forget, but provides no offset tracking, no savepoint coordination, no lag awareness, and no `StreamContext` for downstream tasks to inspect. Every team builds this plumbing manually.

**Use a dedicated orchestrator like Prefect or Dagster:** Both have streaming-adjacent features but require migrating off Airflow entirely. This provider brings streaming-awareness to teams already running Airflow in production — zero migration cost.

**Confluent Cloud / AWS Managed Streaming + their own orchestration:** Vendor-locked and still doesn't solve the batch+stream unification problem in the DAG layer.

---

## References

- AIP-72 (Dataset / Data-Aware Scheduling) — related prior art
- Flink REST API: https://nightlies.apache.org/flink/flink-docs-release-1.19/docs/ops/rest_api/
- Kafka exactly-once semantics: https://kafka.apache.org/documentation/#semantics
- Spark Structured Streaming: https://spark.apache.org/docs/latest/structured-streaming-programming-guide.html
- Existing Kafka provider: `apache-airflow-provider-apache-kafka`
- Existing Amazon provider: `apache-airflow-provider-amazon` (Kinesis hooks)
