# AIP-04: Airflow Event-Driven Orchestration Layer

## Status
Draft

## Authors
- Atam Prakash

## Created
2026-03-26

---

## Abstract

This AIP proposes an event-driven orchestration layer for Apache Airflow, enabling DAGs and tasks to be triggered based on real-time events (e.g., messages, data changes, webhooks) instead of or in addition to time-based schedules. The goal is to make Airflow responsive, reduce latency, and better integrate with modern streaming and event-based architectures.

---

## Motivation

Airflow is primarily a schedule-based orchestrator. While sensors and external triggers exist, they are:

- Inefficient (polling-based sensors)
- Hard to scale
- Not truly event-driven
- Difficult to integrate with modern systems (Kafka, CDC, webhooks)

Modern data platforms increasingly rely on:
- Event streams (Kafka, Pub/Sub)
- Change Data Capture (CDC)
- Microservices emitting events

Airflow lacks a native abstraction for consuming and reacting to such events.

---

## Goals

1. Enable DAG and task triggering based on external events
2. Support multiple event sources (Kafka, HTTP, DB changes)
3. Provide low-latency execution without polling
4. Ensure reliability (at-least-once or exactly-once semantics where possible)
5. Maintain compatibility with existing Airflow DAGs

---

## Non-Goals

- Replacing streaming engines (e.g., Flink, Spark Streaming)
- Full event processing (windowing, aggregations)
- Guaranteeing global exactly-once semantics across all systems

---

## Proposal

### 1. Event Abstraction

Introduce a first-class Event concept:

- Event Type (e.g., "table.updated", "file.arrived")
- Payload (JSON)
- Metadata (timestamp, source, partition, offset)

Events can be produced externally or internally.

---

### 2. Event Triggers for DAGs

New trigger type:

```python
with DAG(
    dag_id="event_driven_dag",
    schedule=None,
    event_trigger={
        "type": "kafka",
        "topic": "orders",
    },
):
    ...
```

Supported sources:
- Kafka
- HTTP/Webhooks
- Cloud Pub/Sub
- Database CDC

---

### 3. Event Listener Service

A new Airflow component:

- Subscribes to event sources
- Converts events into DAG runs
- Handles offset tracking and retries

This can run as a separate service or as part of the scheduler.

---

### 4. Event Context Injection

Expose event payload to tasks:

```python
@task
def process(event):
    print(event["payload"])
```

Event becomes part of execution context similar to XCom.

---

### 5. Backpressure and Rate Control

Mechanisms:
- Max concurrent event-triggered DAG runs
- Queueing and batching
- Drop or debounce policies

---

### 6. Reliability Semantics

Options:
- At-least-once (default)
- Deduplication via event IDs
- Checkpointing (Kafka offsets, etc.)

---

### 7. UI Integration

Enhancements:
- View event-triggered DAGs
- Inspect event payloads per run
- Replay events

---

## Architecture

Components:

1. Event Listener Service
2. Event Source Adapters
3. DAG Trigger Engine
4. Metadata Store (offsets, checkpoints)
5. UI + CLI

Flow:

1. Event arrives (e.g., Kafka)
2. Listener consumes event
3. Event mapped to DAG trigger
4. DAG run created with event context
5. Tasks execute using event data

---

## API Changes

### DAG Definition

Add `event_trigger` parameter

### CLI

- `airflow events listen`
- `airflow events replay`

---

## Storage Design

Store:
- Event metadata
- Offsets/checkpoints
- Event-to-DAG mapping

---

## Performance Considerations

- High-throughput event ingestion
- Horizontal scaling of listeners
- Efficient batching

---

## Security Considerations

- Authentication for event sources
- Payload validation
- Access control for event data

---

## Alternatives Considered

1. Sensors-based approach
   - Inefficient due to polling

2. External orchestrators
   - Adds operational complexity

---

## Open Questions

- Should event listener be core or optional plugin?
- How to unify different event sources?
- What level of ordering guarantees should be provided?

---

## Future Work

- Native streaming task support
- Integration with lineage systems
- Event-driven retries and compensation logic

---

## Conclusion

This proposal introduces a native event-driven orchestration capability in Airflow, enabling it to better support modern real-time data architectures while preserving its core strengths in workflow orchestration.

