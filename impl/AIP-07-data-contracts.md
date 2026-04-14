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

# AIP-07 — Data contracts provider (implementation notes)

This document describes the **Phase 1** implementation split across:

- **`apache-airflow-providers-data-contracts`** — `providers/data/contracts/` (operators, hooks, sensors, models, validators).
- **`apache-airflow-providers-data-contracts-decorators`** — `providers/data/contracts_decorators/` (optional TaskFlow decorators on top of the standard provider).

The normative design remains in `ideas/AIP-07-data-contract-provider.md`.

Install the core provider for operators only; add the decorators package (or the Airflow extra `data.contracts.decorators`) when you want `@task.contract_*` helpers.

## Scope (implemented)

- **Models**: `DataContract`, `SchemaField`, `ContractViolation`, `ContractResult`, `SchemaDiff`.
- **Validators**: schema, completeness (row counts), freshness (`stats["data_as_of"]`), SLA (DAG run duration vs `sla_completion_minutes`).
- **Hooks**:
  - `DataHubCatalogHook` (`conn_type=datahub`) — reads dataset entities via DataHub OpenAPI v3; optional embedded contract JSON in `customProperties.airflow_data_contract`; otherwise derives a minimal contract from `schemaMetadata` + numeric custom properties.
  - `YamlDataContractHook` (`conn_type=data_contract_yaml`) — **catalog-lite**: `extras.contracts` maps `dataset_urn` → YAML/JSON file path.
- **Operators**: `ContractValidateOperator`, `ContractPublishOperator`, `ContractBreachGuardOperator`, `ContractTriggerUserGuardOperator` (allow-list for `DagRun.triggering_user_name` from inline `allowed_users` or from a contract's `allowed_trigger_users` resolved by `dataset_urn` via the platform-managed default YAML catalog connection; optional DAG pause on violation).
- **Sensor**: `ContractReadySensor`.
- **Shared runners** (core package, imported by operators and by decorators): `contract_validate_runner`, `contract_publish_runner`, `contract_breach_runner`, `contract_ready_runner` — keep validation/publish/breach/poke logic in one place.
- **Task decorators** (decorators distribution only): registered as `@task.contract_validate`, `@task.contract_publish`, `@task.contract_breach_guard`, `@task.contract_ready`, `@task.contract_trigger_user_guard`. Factories live under `airflow.providers.data.contracts_decorators.decorators` (`contract_validate_task`, `contract_publish_task`, `contract_breach_guard_task`, `contract_ready_task`, `contract_trigger_user_guard_task`). For plain `@task` with a separate layer, stack the matching stackable helper on the inner callable: `contract_validate`, `contract_publish`, `contract_breach_guard`, `contract_ready` (one-shot poke; fails instead of rescheduling), or `contract_trigger_user_guard`. The contract-ready **sensor** calls your callable **each poke**; it must return the dataset URN string to check (use a constant function for a fixed URN).
- **Deferred** (future phases / follow-up): metadata DB breach tables, UI panel, `airflow contracts` CLI, full GMS aspect writes for publish/breach, OpenMetadata/Atlan hooks, `SchemaEvolutionOperator`, `SchemaMatchSensor`.

## Package layout

### Core (`providers/data/contracts/`)

```text
providers/data/contracts/
├── provider.yaml
├── pyproject.toml
├── src/airflow/providers/data/contracts/
│   ├── models/
│   ├── validators/
│   ├── hooks/
│   ├── operators/
│   ├── sensors/
│   ├── contract_validate_runner.py
│   ├── contract_publish_runner.py
│   ├── contract_breach_runner.py
│   ├── contract_ready_runner.py
│   └── contract_trigger_user_runner.py
└── tests/unit/data/contracts/
```

### Decorators (`providers/data/contracts_decorators/`)

```text
providers/data/contracts_decorators/
├── provider.yaml
├── pyproject.toml
├── src/airflow/providers/data/contracts_decorators/
│   ├── decorators/
│   │   ├── contract_validate.py
│   │   ├── contract_publish.py
│   │   ├── contract_breach_guard.py
│   │   ├── contract_ready.py
│   │   ├── contract_trigger_user_guard.py
│   │   ├── _python_operator_execute.py
│   │   └── _stackable_under_task.py
│   └── get_provider_info.py
└── tests/unit/data/contracts_decorators/
```

## Examples (repo)

| Path | What it shows |
|------|----------------|
| `example/aip-07/minimal_standalone/` | Minimal validation with `ContractValidateOperator` + local YAML (`contract_yaml_path`); `dags/simple_trigger_user_guard.py` shows `ContractTriggerUserGuardOperator` with `dataset_urn` only (see `README.md` for catalog connection). |
| `example/aip-07/minimal_decorators/` | TaskFlow examples using stacked annotations (`@task` + `contract_validate` / `contract_publish` / `contract_breach_guard` / `contract_trigger_user_guard`) and `contract_ready_task` sensor. Catalog-backed DAGs need a `data_contract_yaml` connection. |
| `example/aip-07/example1/` | Postgres producer/consumer with operators, SQL, and YAML catalog connection. |

## Connections

### DataHub (`datahub`)

- **Host** or `extras.gms_url`: GMS base URL (no trailing slash required).
- **Password** or `extras.token`: bearer token for `Authorization: Bearer …`.
- `extras.timeout_sec` (optional, default 30).

Embedded full contract (recommended until native contract aspects are wired):

```json
{
  "airflow_data_contract": "{\"dataset_urn\":\"urn:li:dataset:(...)\",\"schema\":[...],\"min_row_count\":1000}"
}
```

Without `airflow_data_contract`, the hook builds a contract from `schemaMetadata.fields` and optional custom properties:
`contract_status`, `contract_version`, `schema_version`, `freshness_max_age_minutes`, `min_row_count`, `max_row_count`, `sla_completion_minutes`, `last_validated_at`, `last_breach_at`.

### Catalog-lite YAML (`data_contract_yaml`)

```json
{
  "contracts": {
    "urn:local:prod.orders": "/opt/airflow/contracts/prod.orders.yaml"
  },
  "contracts_base_dir": "/opt/airflow"
}
```

Paths in `contracts` may be absolute or relative to `contracts_base_dir` / connection `host` (used as base when set).

## How to use

### Producer: validate then publish

1. Upstream task returns XCom dict, for example:

   ```python
   {
       "row_count": 1_000_000,
       "schema": [{"name": "order_id", "type": "STRING", "nullable": False}],
       "data_as_of": "2026-03-31T06:00:00+00:00",
   }
   ```

2. `ContractValidateOperator` pulls that XCom key, loads the contract, runs validators, optionally reports breaches, and pushes `ContractResult` to XCom (`result_xcom_key`).

3. `ContractPublishOperator` calls `emit_lineage` and `update_contract_status` on the hook (today **logs** for DataHub; real writes depend on your GMS / ingest setup).

`ContractValidateOperator` supports `contract_yaml_path` to bypass the catalog and load a file directly (still uses `catalog_conn_id` for breach reporting when enabled).

### TaskFlow decorators (optional package)

Equivalent patterns using `apache-airflow-providers-data-contracts-decorators`:

- **Validate** — `contract_validate_task`: decorated callable returns the stats `dict` (combines “build stats” + validate in one task) and resolves contracts from the platform-managed catalog mapping for `dataset_urn`. Stack `contract_validate` under plain `@task` for the same behavior split across decorators.
- **Publish** — `contract_publish_task`: callable returns stats; operator always uses the catalog hook (configure `data_contract_yaml` or DataHub). Stack `contract_publish` under `@task` when needed.
- **Breach guard** — `contract_breach_guard_task`: callable returns `list[str]` URNs to check. Stack `contract_breach_guard` under `@task` when needed.
- **Ready** — `contract_ready_task`: callable returns the dataset URN `str` for each poke (unlike `ContractReadySensor`, which takes a static `dataset_urn`). Stack `contract_ready` under `@task` for a **one-shot** readiness check (fails if not ready; no reschedule).
- **Trigger user** — `contract_trigger_user_guard_task`: pass **`allowed_users`**, or **`dataset_urn`** (catalog supplied by the platform; connection/path are system-managed). Stack `contract_trigger_user_guard` innermost on the callable for plain `@task`.

### Consumer: wait then guard

1. `ContractReadySensor` waits until status is `ACTIVE` and, if `min_update_time` is set, until `last_validated_at` is at least that timestamp (requires `last_validated_at` on the contract).

2. `ContractBreachGuardOperator` fails, skips, or warns when any listed URN has status `BREACHED`. Optional `override_var` names an Airflow Variable (`true`/`1`/`yes`/`on`) to bypass the guard.

### Manual-run user gate

Contract YAML may define `allowed_trigger_users: [user, …]` (list of `DagRun.triggering_user_name` values). `ContractTriggerUserGuardOperator` accepts **`allowed_users`**, or **`dataset_urn`** with the platform default `data_contract_yaml` connection (`data_contract_yaml_default`) mapping URNs to files; DAG code does not pass catalog connection IDs or YAML paths for trigger-user checks. **Scheduled runs** often have no triggering user; use `when_triggering_user_missing` (`allow` / `fail` / `skip`). On mismatch, `on_unauthorized` can `fail`, `skip`, `warn`, or **`pause_dag`** (sets `DagModel.is_paused` for this DAG, then fails the task).

TaskFlow: same parameters as `ContractTriggerUserGuardOperator`; the guard runs then your callable in one task.

## Testing

```bash
uv run --project providers/data/contracts pytest providers/data/contracts/tests/unit/data/contracts -xvs
uv run --project providers/data/contracts_decorators pytest providers/data/contracts_decorators/tests/unit/data/contracts_decorators -xvs
```

After changing `provider.yaml` dependencies or adding the provider to the workspace, run the repository scripts that refresh generated metadata (see `generated/README.md` and `contributing-docs/12_provider_distributions.rst`).
