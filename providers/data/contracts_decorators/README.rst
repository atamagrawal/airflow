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

==========================================
Data contracts TaskFlow decorators provider
==========================================

This package registers TaskFlow helpers on top of ``apache-airflow-providers-data-contracts``.
It depends on ``apache-airflow-providers-standard`` (Python / sensor decorator base classes).

Registered names and Python factories:

* ``@task.contract_validate`` — ``contract_validate_task`` — callable returns contract **stats** ``dict``. Stack ``contract_validate`` (inner) under plain ``@task`` (outer) for the same validation after your callable.
* ``@task.contract_publish`` — ``contract_publish_task`` — callable returns stats for lineage / status update. Stack ``contract_publish`` under ``@task`` for the same publish step.
* ``@task.contract_breach_guard`` — ``contract_breach_guard_task`` — callable returns ``list[str]`` dataset URNs. Stack ``contract_breach_guard`` under ``@task`` for the same guard.
* ``@task.contract_ready`` — ``contract_ready_task`` — **each poke** invokes the callable; it must return the dataset URN ``str`` to check (constant lambda for a fixed URN). For a **one-shot** readiness check in a normal task (fail if not ready, no reschedule), stack ``contract_ready`` under ``@task``.
* ``@task.contract_trigger_user_guard`` — ``contract_trigger_user_guard_task`` — pass **``allowed_users``**, or **``dataset_urn``** (platform catalog maps the URN to contract YAML via system-managed connection/path); the callable is normal task code and the guard runs first. To use plain ``@task`` instead, stack ``contract_trigger_user_guard`` (inner) under ``@task`` (outer).

For catalog-backed decorators, connection id / YAML path are system-managed; DAG code passes dataset URNs and stats/URN lists only.

Import from ``airflow.providers.data.contracts_decorators.decorators``.

Install together with the base provider, for example:

.. code-block:: bash

   pip install apache-airflow-providers-data-contracts apache-airflow-providers-data-contracts-decorators

Or use the Airflow extra ``data.contracts.decorators``.

See ``impl/AIP-07-data-contracts.md`` and ``example/aip-07/minimal_decorators/`` in the Airflow repo.
