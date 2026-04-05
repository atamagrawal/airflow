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
Minimal **consumer** flow using ``contract_ready_task`` and ``contract_breach_guard_task``.

Mirrors the idea of ``example/aip-07/example1/dags/aip07_consumer.py`` without Postgres:
wait until the YAML-backed contract is ``ACTIVE``, run the breach gate, then a placeholder task.

Requires the same ``data_contract_yaml`` connection as the publish example. See ``README.md``.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.providers.data.contracts_decorators.decorators.contract_breach_guard import (
    contract_breach_guard_task,
)
from airflow.providers.data.contracts_decorators.decorators.contract_ready import contract_ready_task
from airflow.sdk import DAG, task

DATASET_URN = "urn:example:sample_dataset"
CATALOG_CONN_ID = os.environ.get("AIP07_YAML_CATALOG_CONN_ID", "data_contract_yaml_default")


@contract_ready_task(
    catalog_conn_id=CATALOG_CONN_ID,
    poke_interval=5,
    timeout=120,
    mode="poke",
    task_id="wait_for_contract",
)
def wait_for_sample_dataset() -> str:
    """Return the dataset URN to poll (constant here — could be dynamic)."""
    return DATASET_URN


@contract_breach_guard_task(
    catalog_conn_id=CATALOG_CONN_ID,
    on_breach="fail",
    task_id="guard_contracts",
)
def guard_upstream_contracts() -> list[str]:
    """URNs to check (same single dataset for this minimal demo)."""
    return [DATASET_URN]


@task(task_id="downstream_placeholder")
def downstream_placeholder() -> str:
    """Replace with real consumer work."""
    return "ok"


with DAG(
    dag_id="simple_data_contract_consumer_decorators",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal", "decorators"],
    doc_md=__doc__,
) as _:
    wait_for_sample_dataset() >> guard_upstream_contracts() >> downstream_placeholder()
