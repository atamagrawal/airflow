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

# AIP-07 Data Contracts — Minimal decorators example

This folder mirrors ``example/aip-07/minimal_standalone/`` but uses TaskFlow helpers from
**apache-airflow-providers-data-contracts-decorators** (``@task.contract_validate``,
``@task.contract_publish``, ``@task.contract_ready``, ``@task.contract_breach_guard``).

```
example/aip-07/minimal_decorators/
├── contracts/
│   └── sample_dataset.yaml          # Same contract as minimal_standalone
├── dags/
│   ├── simple_data_contract_decorators.py           # Validate (no catalog connection)
│   ├── simple_data_contract_publish_decorators.py   # Publish stub (needs YAML connection)
│   └── simple_data_contract_consumer_decorators.py  # Ready sensor + breach guard
└── README.md
```

## Prerequisites

* ``apache-airflow-providers-data-contracts``
* ``apache-airflow-providers-data-contracts-decorators`` (pulls **standard** provider transitively)

Or: ``pip install 'apache-airflow[data.contracts.decorators]'`` from a release that lists this extra.

## DAG: validation only

``simple_data_contract_decorators.py`` — same behavior as ``minimal_standalone`` validation DAG:
``contract_yaml_path`` loads the contract from disk; ``report_breach_to_catalog=False`` avoids
catalog calls on failure. No Airflow connection is required.

## DAGs: publish and consumer

``simple_data_contract_publish_decorators.py`` and ``simple_data_contract_consumer_decorators.py``
call the YAML catalog hook. Add a connection whose extras map the dataset URN to this file
(adjust the YAML path to where the repo is mounted in your environment, e.g. Breeze):

```bash
airflow connections add data_contract_yaml_default \
  --conn-type data_contract_yaml \
  --conn-extra '{
    "contracts": {
      "urn:example:sample_dataset": "/opt/airflow/example/aip-07/minimal_decorators/contracts/sample_dataset.yaml"
    }
  }'
```

Optional: set ``AIP07_YAML_CATALOG_CONN_ID`` if you use a non-default connection id.

## Copy into Airflow

Copy ``dags/`` (and keep ``contracts/`` at the same relative layout), or mount this tree and point
``CONTRACT_YAML`` / connection paths at the real filesystem location.

## Related

* ``impl/AIP-07-data-contracts.md`` — implementation notes (operators, decorators, runners, tests)
* ``example/aip-07/minimal_standalone/`` — operator-based minimal validation
* ``example/aip-07/example1/`` — full Postgres producer / consumer with operators
