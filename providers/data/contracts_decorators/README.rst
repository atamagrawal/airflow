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

* ``@task.contract_validate`` — ``contract_validate_task`` — callable returns contract **stats** ``dict``.
* ``@task.contract_publish`` — ``contract_publish_task`` — callable returns stats for lineage / status update.
* ``@task.contract_breach_guard`` — ``contract_breach_guard_task`` — callable returns ``list[str]`` dataset URNs.
* ``@task.contract_ready`` — ``contract_ready_task`` — **each poke** invokes the callable; it must return the dataset URN ``str`` to check (constant lambda for a fixed URN).

Import from ``airflow.providers.data.contracts_decorators.decorators``.

Install together with the base provider, for example:

.. code-block:: bash

   pip install apache-airflow-providers-data-contracts apache-airflow-providers-data-contracts-decorators

Or use the Airflow extra ``data.contracts.decorators``.

See ``impl/AIP-07-data-contracts.md`` and ``example/aip-07/minimal_decorators/`` in the Airflow repo.
