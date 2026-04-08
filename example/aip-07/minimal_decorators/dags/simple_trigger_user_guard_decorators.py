# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""
Trigger-user guard from **contract YAML** (``allowed_trigger_users``), stackable on any ``@task``.

* **Recommended:** ``with_contract_trigger_user_from_yaml`` directly under ``@task`` so your
  callable is normal task code and the allow-list stays in ``contracts/sample_dataset.yaml``.
* **Alternative:** ``contract_trigger_user_guard_task(contract_yaml_path=...)`` — same YAML
  field; the decorated function is the task body after the guard runs inside one operator.

Requires:

* ``apache-airflow-providers-data-contracts``
* ``apache-airflow-providers-data-contracts-decorators``

Operator-only equivalent: ``example/aip-07/minimal_standalone/dags/simple_trigger_user_guard.py``.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.providers.data.contracts_decorators.decorators.contract_trigger_user_guard import (
    contract_trigger_user_guard_task,
)
from airflow.providers.data.contracts_decorators.decorators.with_contract_trigger_user_from_yaml import (
    with_contract_trigger_user_from_yaml,
)
from airflow.sdk import DAG, task

_EXAMPLE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_YAML = os.path.join(_EXAMPLE_ROOT, "contracts", "sample_dataset.yaml")


@task
@with_contract_trigger_user_from_yaml(CONTRACT_YAML)
def workload_with_stacked_guard() -> str:
    """Return a marker; the YAML allow-list is enforced before this body runs."""
    return "ok"


@contract_trigger_user_guard_task(
    task_id="single_operator_body",
    contract_yaml_path=CONTRACT_YAML,
    when_triggering_user_missing="allow",
    on_unauthorized="fail",
)
def workload_as_single_decorated_task() -> str:
    """Return a marker using the same YAML policy as ``workload_with_stacked_guard``."""
    return "ok"


with DAG(
    dag_id="simple_trigger_user_guard_decorators",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal", "decorators", "trigger-user"],
    doc_md=__doc__,
) as _:
    workload_with_stacked_guard()
    workload_as_single_decorated_task()
