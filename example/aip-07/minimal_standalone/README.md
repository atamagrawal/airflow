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

# AIP-07 Data Contracts — Minimal standalone example

Operator-based minimal examples (no TaskFlow decorators package).

```
example/aip-07/minimal_standalone/
├── contracts/
│   └── sample_dataset.yaml
└── dags/
    ├── simple_data_contract.py           # ContractValidateOperator + contract_yaml_path
    └── simple_trigger_user_guard.py      # ContractTriggerUserGuardOperator + dataset_urn only
```

## Prerequisites

* ``apache-airflow-providers-data-contracts``

## Validation DAG

``simple_data_contract.py`` — loads the contract from disk via ``contract_yaml_path``; no catalog
connection required.

## Trigger user guard DAG

``simple_trigger_user_guard.py`` — DAG passes **``dataset_urn``** only; the platform must provision
``data_contract_yaml_default`` with ``extras.contracts`` mapping that URN to the contract YAML
(``allowed_trigger_users`` is read from the file). Adjust the path to where this repo is mounted
(e.g. Breeze):

```bash
airflow connections add data_contract_yaml_default \
  --conn-type data_contract_yaml \
  --conn-extra '{
    "contracts": {
      "urn:example:sample_dataset": "/opt/airflow/example/aip-07/minimal_standalone/contracts/sample_dataset.yaml"
    }
  }'
```

Manual triggers usually populate ``DagRun.triggering_user_name``; scheduled runs often do not—the
example uses ``when_triggering_user_missing="allow"``.

## TaskFlow equivalent

``example/aip-07/minimal_decorators/`` — same scenarios with decorators; see that folder’s README.

## Related

* ``impl/AIP-07-data-contracts.md`` — implementation notes
