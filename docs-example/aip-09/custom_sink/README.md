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

# AIP-09 Custom Sink Example — Subclassing `SinkProxy`

Shows how to extend the Shadow DAGs system to support **any operator** — not
just TaskFlow `@task` functions.  Here a custom `CsvWriteOperator` is made
shadow-safe by implementing a matching `CsvSinkProxy`.

```
custom_sink/
├── plugins/
│   └── csv_sink_proxy.py       CsvWriteOperator + CsvSinkProxy implementation
└── dags/
    ├── inventory_report.py     Production DAG  (v1 — reorder threshold = 20)
    └── inventory_report_v2.py  Shadow candidate (v2 — threshold = 30, adds days_until_stockout)
```

## When do you need a custom SinkProxy?

The built-in `LocalFileSinkProxy` works by injecting `context["shadow_output_path"]` via the operator's `_pre_execute_hook` and expects `@task` functions (or operators that respect that context key).

For operators that write to a fixed destination determined at construction time (e.g. `output_path=` kwarg, `conn_id=`, `bucket=`), you need a custom proxy that intercepts and redirects that attribute before `execute()` is called.

**Common candidates for custom proxies:**

| Operator type | What to redirect |
|--------------|-----------------|
| CSV/file writers | `output_path` attribute |
| GCS / S3 upload | `bucket` + `object_name` |
| BigQuery insert | `destination_dataset_table` |
| Postgres insert | `target_table` |

## Files

### `plugins/csv_sink_proxy.py`

Contains two classes:

**`CsvWriteOperator`** — minimal operator that writes a list of dicts to a CSV
file.  `output_path` is the production destination.

**`CsvSinkProxy(SinkProxy)`** — proxy that:
1. Validates the operator is a `CsvWriteOperator` (raises `UnsupportedSinkError` otherwise).
2. Replaces `operator.output_path` with `<shadow_sink_root>/<task_id>/output.csv`.
3. Logs the redirection.

The interface every proxy must implement:

```python
class MySinkProxy(SinkProxy):
    def wrap(self, operator: Any, shadow_ctx: ShadowContext) -> Any:
        if not isinstance(operator, MyOperator):
            raise UnsupportedSinkError(type(operator))
        # Redirect the operator's write destination
        operator.output_path = str(shadow_ctx.sink_root / operator.task_id / "output.dat")
        return operator
```

### `dags/inventory_report.py` — production DAG

Generates 5 synthetic inventory rows and writes them as CSV.

Output: `$AIRFLOW_HOME/reports/inventory/<run_id>/inventory.csv`

### `dags/inventory_report_v2.py` — shadow candidate

Same structure, two changes:

| Field | v1 | v2 |
|-------|----|----|
| `reorder_needed` | `stock < 20` | `stock < 30` (threshold raised) |
| `days_until_stockout` | absent | added (new column) |

Uses `ShadowAwareCsvWriteOperator` — a thin subclass that calls
`CsvSinkProxy.wrap(self, shadow_ctx)` inside `execute()` when the shadow
environment variable is set.

## Installation

### 1. Copy the plugin

```bash
cp plugins/csv_sink_proxy.py $AIRFLOW_HOME/plugins/
```

Airflow auto-discovers plugins under `$AIRFLOW_HOME/plugins/` at startup.

### 2. Copy the DAGs

```bash
cp dags/*.py $AIRFLOW_HOME/dags/
```

### 3. Restart the Airflow API server / scheduler

```bash
docker compose restart airflow-scheduler airflow-apiserver
```

### 4. Run and inspect

```bash
airflow dags trigger inventory_report

# Check shadow was auto-registered
airflow shadow list

# Read report after shadow run completes
airflow shadow report --shadow-id <shadow_id>
```

Expected report:

```json
{
  "verdict": "DIVERGED",
  "schema_divergence": [
    {"column": "days_until_stockout", "change": "added"}
  ],
  "value_divergence": [
    {"column": "reorder_needed", ...}
  ]
}
```

## Implementing a proxy for a cloud operator

The same pattern extends to cloud operators.  Example skeleton for a GCS
upload operator:

```python
from airflow.providers.google.cloud.operators.gcs import GCSCreateBucketOperator
from airflow.shadow.sink_proxy import ShadowContext, SinkProxy, UnsupportedSinkError


class GcsSinkProxy(SinkProxy):
    SHADOW_BUCKET_PREFIX = "shadow-"

    def wrap(self, operator, shadow_ctx: ShadowContext):
        if not hasattr(operator, "bucket_name"):
            raise UnsupportedSinkError(type(operator))
        original = operator.bucket_name
        operator.bucket_name = f"{self.SHADOW_BUCKET_PREFIX}{original}"
        return operator
```

The shadow run writes to `shadow-<original-bucket>` instead of the production
bucket.  The `ComparisonEngine` would need a corresponding GCS-aware subclass
to read from that bucket — see `AIP-09-shadow-dags-implementation-notes.md`
for the extension points.

## Related

* `docs-example/aip-09/minimal/` — simplest possible shadow setup (TaskFlow)
* `docs-example/aip-09/etl_refactor/` — full ETL scenario with comparison reports
* `docs-design/aip-09/AIP-09-shadow-dags-implementation-notes.md` — implementation internals
