 .. Licensed to the Apache Software Foundation (ASF) under one
    or more contributor license agreements.  See the NOTICE file
    distributed with this work for additional information
    regarding copyright ownership.  The ASF licenses this file
    to you under the Apache License, Version 2.0 (the
    "License"); you may not use this file except in compliance
    with the License.  You may obtain a copy of the License at

 ..   http://www.apache.org/licenses/LICENSE-2.0

 .. Unless required by applicable law or agreed to in writing,
    software distributed under the License is distributed on an
    "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
    KIND, either express or implied.  See the License for the
    specific language governing permissions and limitations
    under the License.

Package ``apache-airflow-providers-data-contracts``
---------------------------------------------------

`Apache Airflow <https://airflow.apache.org/docs/apache-airflow/stable/index.html>`__
provider for catalog-backed data contract validation (AIP-07).

Python import path: ``airflow.providers.data.contracts``.

Operators, hooks, sensors, and shared runner modules live in this package. Contract YAML may list
``allowed_trigger_users`` for ``ContractTriggerUserGuardOperator`` (via ``contract_yaml_path``) and
the decorators distribution helpers. TaskFlow decorators
(``@task.contract_validate``, ``@task.contract_publish``, ``@task.contract_breach_guard``,
``@task.contract_ready``, ``@task.contract_trigger_user_guard``) live in the sibling distribution
``apache-airflow-providers-data-contracts-decorators`` (import root
``airflow.providers.data.contracts_decorators``).

Install both packages for decorator usage, or use the Airflow extra ``data.contracts.decorators``
where available.

Implementation notes, layout, and examples: ``impl/AIP-07-data-contracts.md`` (repo root).
Example DAGs: ``example/aip-07/minimal_standalone/`` (operators), ``example/aip-07/minimal_decorators/`` (decorators).
