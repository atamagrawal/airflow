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
Trigger-user guard using ``allowed_trigger_users`` from the **platform catalog**.

DAG authors pass **``dataset_urn``** only. Your platform (or product) must provision the default
``data_contract_yaml`` Airflow connection (``data_contract_yaml_default``) with
``extras.contracts`` mapping that URN to the contract YAML — the same pattern as contract
validation. Customers do not configure file paths in the DAG.

This example uses ``urn:example:sample_dataset``; see ``contracts/sample_dataset.yaml`` for the
file the connection should point at.

This DAG uses the stacked style: plain ``@task`` outer with inner
``@contract_trigger_user_guard``.

Requires ``apache-airflow-providers-data-contracts`` and
``apache-airflow-providers-data-contracts-decorators``.

Operator-only equivalent: ``example/aip-07/minimal_standalone/dags/simple_trigger_user_guard.py``.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.data.contracts_decorators.decorators.contract_trigger_user_guard import (
    contract_trigger_user_guard,
)
from airflow.sdk import DAG, task

# Declared by the product / catalog for this dataset — not a path customers edit in DAG code.
SAMPLE_DATASET_URN = "urn:example:sample_dataset"


@task
@contract_trigger_user_guard(
    dataset_urn=SAMPLE_DATASET_URN,
    when_triggering_user_missing="allow",
    on_unauthorized="fail",
)
def protected_workload() -> str:
    """Run only if ``DagRun.triggering_user_name`` is allowed for this dataset contract."""
    return "ok"


with DAG(
    dag_id="simple_trigger_user_guard_decorators",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal", "decorators", "trigger-user"],
    doc_md=__doc__,
) as _:
    protected_workload()
