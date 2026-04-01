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

This document describes the **Phase 1** implementation in
`apache-airflow-providers-data-contracts` (`providers/data/contracts/`).
The normative design remains in `ideas/AIP-07-data-contract-provider.md`.

## Scope (implemented)

- **Models**: `DataContract`, `SchemaField`, `ContractViolation`, `ContractResult`, `SchemaDiff`.
- **Validators**: schema, completeness (row counts), freshness (`stats["data_as_of"]`), SLA (DAG run duration vs `sla_completion_minutes`).
- **Hooks**:
  - `DataHubCatalogHook` (`conn_type=datahub`) — reads dataset entities via DataHub OpenAPI v3; optional embedded contract JSON in `customProperties.airflow_data_contract`; otherwise derives a minimal contract from `schemaMetadata` + numeric custom properties.
  - `YamlDataContractHook` (`conn_type=data_contract_yaml`) — **catalog-lite**: `extras.contracts` maps `dataset_urn` → YAML/JSON file path.
- **Operators**: `ContractValidateOperator`, `ContractPublishOperator`, `ContractBreachGuardOperator`.
- **Sensor**: `ContractReadySensor`.
- **Deferred** (future phases / follow-up): metadata DB breach tables, UI panel, `airflow contracts` CLI, full GMS aspect writes for publish/breach, OpenMetadata/Atlan hooks, `SchemaEvolutionOperator`, `SchemaMatchSensor`.

## Package layout

```text
providers/data/contracts/
├── provider.yaml
├── pyproject.toml
├── src/airflow/providers/data/contracts/
│   ├── models/
│   ├── validators/
│   ├── hooks/
│   ├── operators/
│   └── sensors/
└── tests/unit/data/contracts/
```

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

### Consumer: wait then guard

1. `ContractReadySensor` waits until status is `ACTIVE` and, if `min_update_time` is set, until `last_validated_at` is at least that timestamp (requires `last_validated_at` on the contract).

2. `ContractBreachGuardOperator` fails, skips, or warns when any listed URN has status `BREACHED`. Optional `override_var` names an Airflow Variable (`true`/`1`/`yes`/`on`) to bypass the guard.

## Testing

```bash
uv run --project providers/data/contracts pytest providers/data/contracts/tests/unit/data/contracts -xvs
```

After changing `provider.yaml` dependencies or adding the provider to the workspace, run the repository scripts that refresh generated metadata (see `generated/README.md` and `contributing-docs/12_provider_distributions.rst`).
