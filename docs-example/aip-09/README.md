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

# AIP-09: Shadow DAGs — Examples

Shadow DAGs let you run an experimental pipeline version **alongside** production traffic, compare outputs automatically, and promote (or discard) after reviewing the results — all without ever touching production data.

```
docs-example/aip-09/
├── minimal/          Quick-start: @shadow_dag decorator on a TaskFlow DAG
├── etl_refactor/     Realistic ETL refactor: orders pipeline + output comparison
└── custom_sink/      Advanced: subclassing SinkProxy for a custom operator
```

## Prerequisites

* Airflow 3.2.0 image built with the AIP-09 patch (see `docs-example/Dockerfile`).
* DB migration applied: `airflow db migrate`

## Core concepts

| Concept | One line |
|---------|---------|
| `@shadow_dag` | Decorator that marks a DAG as a shadow candidate and embeds config as a DAG tag |
| `LocalFileSinkProxy` | Redirects operator output to `$AIRFLOW_HOME/shadow/<shadow_id>/<run_id>/` |
| `ComparisonEngine` | Reads shadow + production JSONL outputs and returns a structured `ComparisonReport` |
| `ShadowDagService` | Create / list / promote / discard shadow experiments |
| `airflow shadow` CLI | Command group for managing shadow experiments from the terminal |
| Shadow lifecycle | REGISTERED → ACTIVE → REVIEW → PROMOTED \| DISCARDED → CLEANED_UP |

## Pick an example

### `minimal/` — decorator quick-start

Best starting point.  Two TaskFlow DAGs: one production, one shadow.  No external
dependencies.  Shows the `@shadow_dag` stacking pattern and shadow-aware output
writing.

### `etl_refactor/` — ETL refactor with comparison

Realistic scenario: a daily orders ETL is being re-written with a new normalization
algorithm.  Production and shadow both emit JSONL rows that the `ComparisonEngine`
can diff.  Includes a utility DAG that reads and pretty-prints the latest comparison
report.  Also shows all CLI commands end-to-end.

### `custom_sink/` — custom SinkProxy

Shows how to subclass `SinkProxy` for an operator that writes to a custom
destination (a simple CSV writer here).  The proxy redirects CSV writes to a
shadow subdirectory so production files are never touched.

## Quick CLI reference

```bash
# Register a shadow experiment manually (optional — @shadow_dag does this automatically)
airflow shadow create \
  --production-dag-id orders_daily \
  --candidate-dag-id orders_daily_v2 \
  --ttl-days 7 \
  --divergence-alert-pct 5.0

# List all active shadows
airflow shadow list --status ACTIVE

# View the latest comparison report
airflow shadow report --shadow-id <shadow_id>

# Promote (candidate replaces production)
airflow shadow promote --shadow-id <shadow_id>

# Discard (candidate is abandoned)
airflow shadow discard --shadow-id <shadow_id>
```

## REST API quick reference

```bash
BASE=http://localhost:8080/api/v2
AUTH="-H 'Authorization: Basic $(echo -n admin:admin | base64)'"

# List all shadow experiments
curl $AUTH $BASE/shadow-dags

# Create manually
curl $AUTH -X POST $BASE/shadow-dags \
  -H 'Content-Type: application/json' \
  -d '{"production_dag_id":"orders_daily","candidate_dag_id":"orders_daily_v2","ttl":"7d"}'

# Get latest comparison report
curl $AUTH $BASE/shadow-dags/<shadow_id>/report
```

## Related

* `docs-design/aip-09/AIP-09-shadow-dags-implementation-notes.md` — implementation details
* `docs-design/aip-09/AIP-09-shadow-dags-docker-deployment.md` — Docker build & deployment guide
* `docs-ideas/AIP-09-shadow-dags.md` — original design document
