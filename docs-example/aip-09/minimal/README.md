<!--
 Licensed to the Apache Software Foundation (ASF) under one
 or more contributor license agreements.  See the NOTICE file
 distributed with this work for additional information
 regarding copyright ownership.  The ASF licenses this file
 to you under the Apache License, Version 2.0 (the
 "License"); you may not use this file except in compliance
 with the License.  You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing,
 software distributed under the License is distributed on an
 "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 KIND, either express or implied.  See the License for the
 specific language governing permissions and limitations
 under the License.
-->

# AIP-09 Minimal Example — `@shadow_dag` decorator quick-start

The simplest possible Shadow DAG setup: two TaskFlow DAGs with no external
dependencies, all data generated in memory.

```
minimal/
└── dags/
    ├── daily_summary.py      Production DAG  (v1 transform — groups events by user, sums amount)
    └── daily_summary_v2.py   Shadow candidate (v2 transform — adds transaction_count, caps u3 at $50)
```

## What happens end-to-end

```
DAG parse
  └── @shadow_dag on daily_summary_v2 injects __shadow__:<json> tag
      └── collection.py detects tag → ShadowDagService.create() → shadow_dag table row

Trigger daily_summary
  └── Scheduler creates production DagRun
      └── _create_shadow_dag_runs() spawns a parallel DagRun for daily_summary_v2
          └── LocalFileSinkProxy injects shadow_output_path into task context
              └── transform_v2 writes to $AIRFLOW_HOME/shadow/<shadow_id>/<run_id>/transform_v2/output.jsonl

After shadow run completes
  └── ComparisonEngine reads both JSONL files
      └── Detects: +1 column (transaction_count), u3 total_amount differs → verdict: DIVERGED
          └── ShadowDagService.record_comparison() stores report on shadow_dag row
```

## Files

### `daily_summary.py` — production DAG

* `extract` — generates 20 synthetic events for the logical date.
* `transform` — groups by `user_id`, sums `amount`, writes
  `$AIRFLOW_HOME/prod_output/daily_summary/<run_id>/transform/output.jsonl`.
* `load` — logs row count.

### `daily_summary_v2.py` — shadow candidate

Identical structure, different `transform_v2`:
* Adds `transaction_count` per user (new field → schema divergence in report).
* Caps `u3`'s `total_amount` at 50.0 (new business rule → value divergence).

Uses the shadow-aware output pattern — checks `context["shadow_output_path"]`
first (injected by `LocalFileSinkProxy`) and falls back to a local path when
running standalone.

## Running the example

### 1. Copy DAGs

```bash
cp dags/*.py $AIRFLOW_HOME/dags/
```

### 2. Wait for DAG parse

The next parse cycle auto-registers the shadow experiment.  Verify:

```bash
airflow shadow list
# REGISTERED  daily_summary → daily_summary_v2   expires: 2025-xx-xx
```

### 3. Activate the shadow

```bash
airflow shadow list        # note the shadow_id
airflow dags unpause daily_summary
airflow dags unpause daily_summary_v2
```

Activate by transitioning from REGISTERED → ACTIVE:

```bash
# The scheduler auto-activates once both DAGs are unpaused and the
# first production run triggers a shadow run.
# Or activate manually via REST:
curl -X POST http://localhost:8080/api/v2/shadow-dags/<shadow_id>/activate \
  -H "Authorization: Basic $(echo -n admin:admin | base64)"
```

### 4. Trigger a production run

```bash
airflow dags trigger daily_summary
```

The scheduler creates a parallel shadow run for `daily_summary_v2`.

### 5. Inspect the report

```bash
airflow shadow report --shadow-id <shadow_id>
```

Expected output:

```json
{
  "verdict": "DIVERGED",
  "row_count_prod": 10,
  "row_count_shadow": 10,
  "row_count_delta_pct": 0.0,
  "schema_divergence": [{"column": "transaction_count", "change": "added"}],
  "value_divergence": [{"column": "total_amount", ...}],
  "sample_diff_rows": [...]
}
```

### 6. Promote or discard

```bash
# Happy with v2 → promote
airflow shadow promote --shadow-id <shadow_id>

# Not happy → discard
airflow shadow discard --shadow-id <shadow_id>
```

## Shadow-aware task pattern

The key pattern that lets `ComparisonEngine` diff the outputs:

```python
@task
def my_task(**context) -> int:
    rows = compute_rows()

    # LocalFileSinkProxy injects this during shadow runs.
    shadow_output_path = context.get("shadow_output_path")
    output_path = Path(shadow_output_path) if shadow_output_path else Path("/tmp/fallback/output.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    return len(rows)
```

During production runs `shadow_output_path` is `None`, so the task writes to
its normal destination.  During shadow runs the proxy injects the isolated sink
path.
