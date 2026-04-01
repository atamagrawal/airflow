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

# AIP-07 Data Contracts — Example 1 (PostgreSQL)

This example implements a **realistic producer / consumer flow** against a
PostgreSQL table ``warehouse.daily_orders``: SQL files define DDL and load,
the producer builds contract statistics from ``information_schema`` and row
counts, and the consumer runs downstream aggregates in the same database.

```
example/aip-07/example1/
├── contracts/
│   └── daily_orders.yaml      # Data contract (schema + SLAs)
├── sql/
│   ├── 001_create_daily_orders.sql   # CREATE SCHEMA / TABLE / INDEX
│   └── 002_load_daily_orders.sql     # Daily DELETE + INSERT (templated {{ ds }})
├── dags/
│   ├── aip07_producer.py      # DDL → load → stats → validate → publish
│   └── aip07_consumer.py      # sensor → breach guard → SQL report
└── README.md
```

## Components

| Piece | Role |
|---|---|
| ``001_create_daily_orders.sql`` | Creates ``warehouse.daily_orders`` (``VARCHAR``, ``DATE``, ``NUMERIC``, nullable ``status``). |
| ``002_load_daily_orders.sql`` | Idempotent load for the logical date using Airflow macros. |
| ``collect_contract_stats`` | Queries ``COUNT(*)`` and column metadata; maps PG types to contract types (``NUMERIC`` → ``FLOAT``, everything else used here → ``STRING``). |
| ``ContractValidateOperator`` | Validates XCom stats against ``daily_orders.yaml``. |
| ``report_revenue_by_day`` | Consumer task: 7-day revenue rollup from Postgres. |

### Sensor note (YAML catalog)

``ContractReadySensor`` is configured with ``min_update_time=None`` because the
file-based YAML hook does not persist ``last_validated_at``.  For interval-based
readiness against validation timestamps, use a catalog integration (for example
DataHub) that updates ``last_validated_at``.

---

## Prerequisites

* Airflow with **PostgreSQL** provider and **common SQL** provider (typical in Breeze / full installs).
* A running PostgreSQL instance reachable from Airflow.
* **apache-airflow-providers-data-contracts** (this repo’s provider).

Optional: set ``AIP07_POSTGRES_CONN_ID`` if you do not use the default
``postgres_default`` connection id.

---

## Quick start — provider unit tests (no database)

```bash
uv run --project providers/data/contracts \
  pytest providers/data/contracts/tests/unit/ -xvs
```

---

## Run end-to-end in Breeze

### 1. Enter Breeze

```bash
breeze
```

Use Breeze’s PostgreSQL backend or add a **postgres** connection pointing at
your database (host, login, database name, etc.).

### 2. Postgres connection

If needed, add or adjust the default connection (example — adjust host/user/db):

```bash
airflow connections delete postgres_default 2>/dev/null || true
airflow connections add postgres_default \
  --conn-type postgres \
  --conn-host localhost \
  --conn-login airflow \
  --conn-password airflow \
  --conn-schema airflow \
  --conn-port 5432
```

Ensure the database user can create schemas and tables.

### 3. YAML catalog connection

```bash
airflow connections add data_contract_yaml_default \
  --conn-type data_contract_yaml \
  --conn-extra '{
    "contracts": {
      "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.daily_orders,PROD)": "/opt/airflow/example/aip-07/example1/contracts/daily_orders.yaml"
    }
  }'
```

### 4. Install DAGs and SQL (paths must resolve inside the container)

```bash
cp /opt/airflow/example/aip-07/example1/dags/*.py /opt/airflow/dags/
# SQL files are loaded from paths computed in the DAG relative to example1/
```

The producer DAG resolves SQL paths from the file location under
``/opt/airflow/example/aip-07/example1/sql/`` — keep the **example1** tree
available at that path (default Breeze mount of the repo).

### 5. Run the producer, then the consumer

```bash
airflow dags test aip07_producer 2025-01-15
airflow dags test aip07_consumer 2025-01-15
```

---

## Failure scenarios (edit SQL or data)

### Schema mismatch

Drop a column in the database (or change ``002_load_daily_orders.sql`` to stop
inserting into one column) **without** updating the YAML contract — validation
should report a missing column.

### Completeness / row count

Temporarily set ``DELETE`` to remove all rows for ``{{ ds }}`` without
inserting, or lower ``min_row_count`` in the YAML to see pass/fail behavior.

### Breach guard

Set ``status: BREACHED`` in ``daily_orders.yaml`` and run ``aip07_consumer``;
``breach_guard`` should fail until you restore ``ACTIVE``.

---

## Direct Python check (contract file only)

```python
from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook

contract = YamlDataContractHook.load_contract_from_file(
    "example/aip-07/example1/contracts/daily_orders.yaml"
)
print(contract.contract_id, contract.schema)
```

Run with:

```bash
uv run --project providers/data/contracts python dev/your_script.py
```
