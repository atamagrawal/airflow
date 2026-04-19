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
Minimal **consumer** flow using TaskFlow contract helpers.

Mirrors the idea of ``example/aip-07/example1/dags/aip07_consumer.py`` without Postgres:
wait until the YAML-backed contract is ``ACTIVE``, run the breach gate(s), then a placeholder task.

* **Ready:** ``@contract_ready_task`` is a sensor (reschedules until the contract is ready). For a
  single Python task that **fails** if the dataset is not ready yet, use ``@task`` with inner
  ``@contract_ready`` instead (see provider docs).
* **Breach guard:** uses stacked ``@task`` + ``@contract_breach_guard`` after the sensor.

Requires the same ``data_contract_yaml`` connection as the publish example. See ``README.md``.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.data.contracts_decorators.decorators.contract_breach_guard import (
    contract_breach_guard,
)
from airflow.providers.data.contracts_decorators.decorators.contract_ready import contract_ready_task
from airflow.sdk import DAG, task

DATASET_URN = "urn:example:sample_dataset"


def _upstream_urns() -> list[str]:
    """URNs to check (single dataset in this minimal demo)."""
    return [DATASET_URN]


@contract_ready_task(
    poke_interval=5,
    timeout=120,
    mode="poke",
)
def wait_for_sample_dataset() -> str:
    """Return the dataset URN to poll (constant here — could be dynamic)."""
    return DATASET_URN


@task
@contract_breach_guard(
    on_breach="fail",
)
def guard_upstream_contracts() -> list[str]:
    """Return upstream URNs for the breach guard under ``@task`` + ``contract_breach_guard``."""
    return _upstream_urns()


@task
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
    wait = wait_for_sample_dataset()
    guard = guard_upstream_contracts()
    downstream = downstream_placeholder()
    wait >> guard >> downstream
