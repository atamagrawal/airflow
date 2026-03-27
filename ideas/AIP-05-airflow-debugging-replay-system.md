# AIP-03-0001: Airflow Debugging and Replay System

## Status
Draft

## Authors
- Atam Prakash

## Created
2026-03-26

---

## Abstract

This AIP proposes a debugging and replay system for Apache Airflow that enables deterministic re-execution of tasks using captured runtime inputs, outputs, and execution context. The goal is to significantly reduce debugging time, improve reproducibility, and provide time-travel capabilities for DAG runs.

---

## Motivation

Debugging Airflow DAGs is currently challenging due to:

- Lack of reproducibility of task execution
- External dependencies (APIs, databases, files) changing over time
- Limited visibility into intermediate inputs/outputs
- Difficulty in isolating failures locally

Engineers often resort to:
- Manually re-running DAGs
- Adding temporary logs
- Writing custom debug scripts

This leads to slow iteration cycles and unreliable debugging.

---

## Goals

1. Enable deterministic replay of task instances
2. Capture task execution context and dependencies
3. Allow local and remote replay of tasks
4. Provide diffing between successful and failed runs
5. Minimize performance overhead during normal execution

---

## Non-Goals

- Full environment snapshotting (e.g., container-level checkpoints)
- Replacing existing logging/monitoring systems
- Supporting all external systems without adapters

---

## Proposal

### 1. Execution Capture Layer

Introduce a capture mechanism at task runtime:

- Inputs:
  - Task parameters
  - Upstream XComs
  - Environment variables
  - External query inputs (optional)

- Outputs:
  - XCom outputs
  - Task return values
  - Metadata (execution time, retries)

- Context:
  - DAG ID, task ID, execution date
  - Operator type

Captured data will be stored in a pluggable backend (e.g., S3, GCS, local FS).

---

### 2. Replay Engine

A replay engine that:

- Reconstructs execution context
- Injects captured inputs
- Executes task in isolation

Modes:

- Local Replay
- Remote Replay (Airflow worker sandbox)

CLI Example:

```
airflow replay run \
  --dag-id example_dag \
  --task-id transform \
  --execution-date 2026-03-25 \
  --mode local
```

---

### 3. Snapshot Adapters

Adapters for capturing external dependencies:

- SQL Adapter (capture query + result sample)
- HTTP Adapter (capture request/response)
- File Adapter (snapshot input files)

Adapters are optional and configurable.

---

### 4. Diff Engine

Compare two task executions:

- Input differences
- Output differences
- Execution metadata

Use cases:
- Why did this fail today but pass yesterday?

---

### 5. UI Integration

Enhancements to Airflow UI:

- “Replay Task” button
- “Compare Runs” view
- Input/output inspection panel

---

## Architecture

Components:

1. Capture Hook (Operator-level or middleware)
2. Storage Backend (S3/GCS/DB)
3. Replay Service
4. Adapter Plugins
5. UI + CLI

Flow:

1. Task executes
2. Capture layer records inputs/outputs
3. Data stored in backend
4. Replay triggered via CLI/UI
5. Replay engine reconstructs and runs task

---

## API Changes

### New CLI Commands

- `airflow replay run`
- `airflow replay diff`

### Operator Extension

Optional mixin:

```
class ReplayableOperator(BaseOperator):
    enable_capture = True
```

---

## Storage Design

Example structure:

```
/replay/
  dag_id/
    task_id/
      execution_date/
        inputs.json
        outputs.json
        context.json
```

---

## Performance Considerations

- Sampling for large payloads
- Configurable capture levels
- Async upload to storage

---

## Security Considerations

- Mask sensitive data
- Encrypt stored payloads
- Access control for replay data

---

## Alternatives Considered

1. Logging-only approach
   - Insufficient for replay

2. Full container snapshotting
   - Too heavy and complex

---

## Open Questions

- How much data should be captured by default?
- How to handle non-deterministic tasks?
- Should replay run inside Airflow or externally?

---

## Future Work

- Time-travel DAG execution
- Integration with lineage systems
- AI-assisted debugging

---

## References

- Prior art: distributed tracing systems
- Debugging tools in modern data platforms

---

## Conclusion

This proposal introduces a reproducible debugging system for Airflow that improves developer productivity and reliability. By enabling replay and comparison of task executions, it bridges a major gap in the current Airflow ecosystem.

