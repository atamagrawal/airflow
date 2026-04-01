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
AIP-07 example — **Consumer DAG**.

This DAG demonstrates how a downstream team can depend on an upstream
data contract:

1. ``wait_for_orders`` — a :class:`ContractReadySensor` that blocks
   until the ``daily_orders`` contract is ACTIVE and has been validated
   after the current data interval.
2. ``breach_guard`` — a :class:`ContractBreachGuardOperator` that fails
   the run if any upstream dataset is in BREACHED state.
3. ``build_report`` — a placeholder task representing the actual
   consumer workload.

Connection required
~~~~~~~~~~~~~~~~~~~
Same ``data_contract_yaml`` connection as the producer.  See README.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.data.contracts.operators.contract_breach_guard import ContractBreachGuardOperator
from airflow.providers.data.contracts.sensors.contract_ready import ContractReadySensor
from airflow.sdk import DAG, task

DATASET_URN = "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.daily_orders,PROD)"
CATALOG_CONN_ID = "data_contract_yaml_default"


@task
def build_report() -> str:
    """Build a report from daily_orders — only runs when the contract is healthy."""
    return "Report built successfully from daily_orders"


with DAG(
    dag_id="aip07_consumer",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["aip-07", "data-contracts", "consumer"],
    doc_md=__doc__,
) as dag:
    wait = ContractReadySensor(
        task_id="wait_for_orders",
        catalog_conn_id=CATALOG_CONN_ID,
        dataset_urn=DATASET_URN,
        min_update_time="{{ data_interval_end | ts }}",
        poke_interval=30,
        timeout=3600,
        mode="poke",
        fail_on_breach=True,
    )

    guard = ContractBreachGuardOperator(
        task_id="breach_guard",
        catalog_conn_id=CATALOG_CONN_ID,
        dataset_urns=[DATASET_URN],
        on_breach="fail",
    )

    report = build_report()

    wait >> guard >> report
