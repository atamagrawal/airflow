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
AIP-07 example — **Consumer DAG** (PostgreSQL).

Downstream analytics pattern:

1. ``wait_for_orders`` — :class:`ContractReadySensor` until the contract is
   ``ACTIVE``.  ``min_update_time`` is left unset because the file-based YAML
   catalog does not persist ``last_validated_at``; use DataHub (or another
   catalog) if you need validation time vs. data interval checks.
2. ``breach_guard`` — :class:`ContractBreachGuardOperator` blocks the run if
   the upstream dataset is ``BREACHED``.
3. ``report_revenue_by_day`` — reads ``warehouse.daily_orders`` in Postgres
   and aggregates revenue for the last 7 days (real downstream SQL).

Connections: same Postgres and YAML catalog as the producer.  See the README.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.providers.data.contracts.operators.contract_breach_guard import ContractBreachGuardOperator
from airflow.providers.data.contracts.sensors.contract_ready import ContractReadySensor
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import DAG, task

DATASET_URN = "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.daily_orders,PROD)"
CATALOG_CONN_ID = "data_contract_yaml_default"
POSTGRES_CONN_ID = os.environ.get("AIP07_POSTGRES_CONN_ID", "postgres_default")


@task
def report_revenue_by_day() -> str:
    """Aggregate recent order revenue from Postgres (consumer workload)."""
    hook = PostgresHook(postgres_conn_id=POSTGRES_CONN_ID)
    sql = """
        SELECT
            order_date::text AS day,
            COUNT(*)::bigint AS orders,
            COALESCE(SUM(amount), 0)::numeric(14, 2) AS revenue_usd
        FROM warehouse.daily_orders
        WHERE order_date >= (CURRENT_DATE - INTERVAL '7 days')
        GROUP BY order_date
        ORDER BY order_date DESC
    """
    rows = hook.get_records(sql)
    lines = [f"{day}: {orders} orders, ${revenue} revenue" for day, orders, revenue in rows]
    summary = "; ".join(lines) if lines else "no rows in the 7-day window"
    return summary


with DAG(
    dag_id="aip07_consumer",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["aip-07", "data-contracts", "consumer", "postgres"],
    doc_md=__doc__,
) as dag:
    wait = ContractReadySensor(
        task_id="wait_for_orders",
        catalog_conn_id=CATALOG_CONN_ID,
        dataset_urn=DATASET_URN,
        min_update_time=None,
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

    report = report_revenue_by_day()

    wait >> guard >> report
