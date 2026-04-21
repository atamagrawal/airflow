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

# AIP-09 ETL Refactor Example — Orders Pipeline

A realistic scenario: a daily orders ETL is being rewritten to fix two bugs
found in production.  The shadow mechanism lets the team validate the new
logic against live production traffic before promoting it.

```
etl_refactor/
└── dags/
    ├── orders_daily.py           Production DAG  (v1 — buggy normalization)
    ├── orders_daily_v2.py        Shadow candidate (v2 — two bugs fixed)
    └── shadow_report_reader.py   Utility DAG to read & log comparison reports
```

## The bugs fixed in v2

| Bug | v1 (production) | v2 (candidate) |
|-----|----------------|----------------|
| Discount not applied | `total_usd = amount * fx` | `total_usd = amount * (1 - discount_pct/100) * fx` |
| Customer PII stored raw | `customer = customer_id` | `customer = blake2b(customer_id, key=HASH_KEY)` |

A new column `discount_applied: bool` is also added in v2.

## Expected comparison report

When both DAGs run for the same day the `ComparisonEngine` will report:

```
verdict         : DIVERGED
row_count_prod  : 50
row_count_shadow: 50
row_count_delta : 0.00 %
schema_divergence:
  - discount_applied (added)
value_divergence:
  - total_usd   (differs for any order with discount_pct > 0)
  - customer    (hash vs raw — different for all rows)
```

This is the intended outcome — the divergences confirm the bugs are fixed.
Once the team reviews the report and approves, they promote the shadow.

## Step-by-step walkthrough

### 1. Copy DAGs

```bash
cp dags/*.py $AIRFLOW_HOME/dags/
```

### 2. Verify auto-registration

On next DAG parse `orders_daily_v2.py` is detected as a shadow candidate and
registered automatically.

```bash
airflow shadow list
```

```
SHADOW_ID                              PRODUCTION       CANDIDATE         STATUS      EXPIRES
shd_orders_daily_20250115              orders_daily     orders_daily_v2   REGISTERED  2025-01-29
```

### 3. Unpause both DAGs

```bash
airflow dags unpause orders_daily
airflow dags unpause orders_daily_v2
```

### 4. Trigger a production run

```bash
airflow dags trigger orders_daily --conf '{"logical_date": "2025-01-15"}'
```

The scheduler's `_create_shadow_dag_runs()` creates a parallel run for
`orders_daily_v2` with `run_id` prefixed `shadow__`.

### 5. Watch the shadow run

```bash
airflow dags list-runs -d orders_daily_v2
```

Shadow runs have `run_id` starting with `shadow__` and `run_type=shadow`.

### 6. Read the comparison report

Via CLI:

```bash
airflow shadow report --shadow-id shd_orders_daily_20250115
```

Via the utility DAG:

```bash
airflow dags trigger shadow_report_reader
```

Via REST API:

```bash
curl http://localhost:8080/api/v2/shadow-dags/shd_orders_daily_20250115/report \
  -H "Authorization: Basic $(echo -n admin:admin | base64)" | python -m json.tool
```

### 7. Review in the UI

Open `http://localhost:8080/dags/orders_daily` → **Shadow Reports** tab.

The Grid view shows the Shadow Lane below each production run row with a
distinct amber border.

### 8. Promote or discard

After confirming the divergences are all expected and intentional:

```bash
# Promote: candidate replaces production
airflow shadow promote --shadow-id shd_orders_daily_20250115

# Or discard: candidate is abandoned
airflow shadow discard --shadow-id shd_orders_daily_20250115
```

## DAG files in detail

### `orders_daily.py` — production DAG

| Task | What it does |
|------|-------------|
| `extract_orders` | Generates 50 synthetic order records (deterministic seed from logical date) |
| `normalize_orders` | Converts to USD (no discount), keeps raw customer ID, writes JSONL |
| `quality_check` | Asserts row count > 0 |
| `write_warehouse` | Simulates warehouse write (no-op) |

Output: `$AIRFLOW_HOME/prod_output/orders_daily/<run_id>/normalize/output.jsonl`

### `orders_daily_v2.py` — shadow candidate

Same task graph, different `normalize_orders_v2`:

* Applies `discount_pct` before FX conversion.
* Hashes `customer_id` with BLAKE2b.
* Adds `discount_applied` boolean field.
* Writes to `context["shadow_output_path"]` (injected by `LocalFileSinkProxy`
  during shadow runs) or falls back to a local path.

### `shadow_report_reader.py` — utility DAG

Trigger manually or on a schedule during the experiment window.

| Task | What it does |
|------|-------------|
| `fetch_active_shadows` | Queries `shadow_dag` table for ACTIVE experiments |
| `print_report` | Logs a formatted summary of each experiment's latest report |
| `check_for_divergence` | Logs a warning when any shadow is DIVERGED (raise uncomment to gate) |

## Lifecycle state machine

```
REGISTERED ──(activate)──► ACTIVE ──(ttl expires)──► REVIEW
                                                         │
                                              ┌──────────┴──────────┐
                                         (promote)             (discard)
                                              │                      │
                                          PROMOTED             DISCARDED
                                                                     │
                                                              (scheduler cleanup)
                                                                     │
                                                               CLEANED_UP
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SHADOW_HASH_KEY` | `dev-only-key` | BLAKE2b key for customer ID hashing (set a real secret in production) |
| `AIRFLOW_HOME` | `~/airflow` | Base directory for JSONL output files |
