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

# AIP-07 Data Contracts — Example

This directory contains a minimal, self-contained example of the
**producer / consumer data-contract pattern** described in AIP-07.

```
example/aip-07/example1/
├── contracts/
│   └── daily_orders.yaml      # YAML data contract definition
├── dags/
│   ├── aip07_producer.py      # Producer DAG: load → validate → publish
│   └── aip07_consumer.py      # Consumer DAG: sensor → breach guard → report
└── README.md                  # This file
```

## What the example shows

| Component | Purpose |
|---|---|
| `daily_orders.yaml` | Defines schema (5 columns), quality rules (row count bounds, freshness SLA), and ownership metadata. |
| `aip07_producer` DAG | Simulates an ETL load, validates output stats against the contract, then publishes status to the catalog. |
| `aip07_consumer` DAG | Waits for the contract to be ACTIVE (`ContractReadySensor`), checks for breaches (`ContractBreachGuardOperator`), then runs a downstream task. |

---

## Quick start — run the unit tests

The fastest way to verify the provider logic (no Airflow instance needed):

```bash
# From the repo root — run all data-contracts unit tests
uv run --project providers/data/contracts \
  pytest providers/data/contracts/tests/unit/ -xvs
```

Key test files:

| File | Covers |
|---|---|
| `test_validators.py` | Schema, completeness, and freshness validation functions |
| `test_contract_validate_operator.py` | `ContractValidateOperator` success and failure paths |
| `test_models.py` | `DataContract` / `SchemaField` dataclass logic |
| `test_local_yaml_hook.py` | `YamlDataContractHook` file loading |

---

## Test with Breeze (full Airflow environment)

### 1. Start Breeze

```bash
breeze
```

### 2. Create the YAML catalog connection

Inside the Breeze shell, create a connection that tells the
`YamlDataContractHook` where to find contract files.  The contract
YAML is mounted at `/opt/airflow/example/aip-07/contracts/daily_orders.yaml`
inside Breeze (the repo root is mounted at `/opt/airflow`).

```bash
airflow connections add data_contract_yaml_default \
  --conn-type data_contract_yaml \
  --conn-extra '{
    "contracts": {
      "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.daily_orders,PROD)": "/opt/airflow/example/aip-07/example1/contracts/daily_orders.yaml"
    }
  }'
```

### 3. Copy DAGs into the DAGs folder

```bash
cp /opt/airflow/example/aip-07/example1/dags/*.py /opt/airflow/dags/
```

### 4. Verify the DAGs parse correctly

```bash
airflow dags list | grep aip07
```

Expected output:

```
aip07_producer  | …  | @daily | …
aip07_consumer  | …  | @daily | …
```

### 5. Test individual tasks

```bash
# Run the producer "load_orders" task
airflow tasks test aip07_producer load_orders 2025-01-01

# Run the contract validation (needs load_orders XCom, so run the full DAG)
airflow dags test aip07_producer 2025-01-01
```

### 6. Trigger via the UI

Open `http://localhost:8080`, unpause both DAGs, and trigger
`aip07_producer`.  Once it succeeds, `aip07_consumer` (if using the
sensor) will proceed.

---

## Test scenarios to try

### Happy path — contract passes

The default `load_orders` stats match the contract perfectly (all 5
columns, 42 rows which is within 1–1,000,000).  The producer DAG
should succeed end-to-end.

### Schema violation — missing column

Edit `dags/aip07_producer.py` and remove the `amount` column from the
stats dict in `load_orders`.  Re-run:

```bash
airflow dags test aip07_producer 2025-01-01
```

The `validate_contract` task should fail with:
`AirflowException: Contract validation failed: Missing required column 'amount'`

### Completeness violation — too few rows

Change `"row_count": 42` to `"row_count": 0` in `load_orders`.
Re-run to see a COMPLETENESS violation.

### Type mismatch

Change `{"name": "amount", "type": "FLOAT", ...}` to
`{"name": "amount", "type": "INTEGER", ...}` in the stats.  The
validator catches the type difference.

### Breach guard — consumer protection

Change the contract YAML status from `ACTIVE` to `BREACHED`, then
run the consumer DAG:

```bash
airflow dags test aip07_consumer 2025-01-01
```

The `breach_guard` task should fail with:
`AirflowException: Upstream contract breach for: urn:li:dataset:…`

---

## Direct Python testing (no Airflow, no Breeze)

You can also validate contract logic directly in a Python script:

```python
from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook
from airflow.providers.data.contracts.validators.contract_validators import (
    validate_schema,
    validate_completeness,
)

# Load the contract from the YAML file
contract = YamlDataContractHook.load_contract_from_file(
    "example/aip-07/example1/contracts/daily_orders.yaml"
)
print(f"Contract: {contract.contract_id}  status={contract.status}")
print(f"Schema fields: {[f.name for f in contract.schema]}")

# Validate some stats
stats = {
    "row_count": 42,
    "schema": [
        {"name": "order_id", "type": "STRING", "nullable": False},
        {"name": "customer_id", "type": "STRING", "nullable": False},
        {"name": "order_date", "type": "STRING", "nullable": False},
        {"name": "amount", "type": "FLOAT", "nullable": False},
        {"name": "status", "type": "STRING", "nullable": True},
    ],
}
schema_violations = validate_schema(contract, stats)
completeness_violations = validate_completeness(contract, stats)
print(f"Schema violations: {len(schema_violations)}")
print(f"Completeness violations: {len(completeness_violations)}")
```

Save the script as `dev/test_contract.py` and run:

```bash
uv run --project providers/data/contracts python dev/test_contract.py
```
