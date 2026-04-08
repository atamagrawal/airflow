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
Gate a DAG run on **who triggered it** using :class:`ContractTriggerUserGuardOperator`.

Allow-list is read from ``allowed_trigger_users`` in
``example/aip-07/minimal_standalone/contracts/sample_dataset.yaml`` via ``contract_yaml_path``.
You can still pass ``allowed_users=[...]`` instead of ``contract_yaml_path`` if you prefer
the list in Python.

``DagRun.triggering_user_name`` is set for many manual triggers (UI, REST, CLI). Scheduled
runs often have no triggering user—use ``when_triggering_user_missing`` (here ``allow`` so
scheduled runs still proceed).

Edit the YAML list to match users in your Airflow metadata DB. Use ``on_unauthorized="pause_dag"``
to pause the DAG on violation (task still fails after pausing).

Requires: ``apache-airflow-providers-data-contracts``.

See also: ``example/aip-07/minimal_decorators/dags/simple_trigger_user_guard_decorators.py``.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.providers.data.contracts.operators.contract_trigger_user_guard import (
    ContractTriggerUserGuardOperator,
)
from airflow.sdk import DAG, task

_EXAMPLE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_YAML = os.path.join(_EXAMPLE_ROOT, "contracts", "sample_dataset.yaml")


@task
def after_guard() -> str:
    """Return a marker after the trigger-user guard passes."""
    return "ok"


with DAG(
    dag_id="simple_trigger_user_guard",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal", "trigger-user"],
    doc_md=__doc__,
) as _:
    guard = ContractTriggerUserGuardOperator(
        task_id="guard_manual_trigger_user",
        contract_yaml_path=CONTRACT_YAML,
        when_triggering_user_missing="allow",
        on_unauthorized="fail",
    )
    _done = after_guard()
    guard >> _done
