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
AIP-07 example — **Producer DAG**.

This DAG simulates a daily ETL pipeline that:

1. ``load_orders`` — produces data and pushes stats (row_count, schema,
   data_as_of) to XCom.
2. ``validate_contract`` — pulls the stats from XCom and validates them
   against the YAML data contract (``contracts/daily_orders.yaml``).
3. ``publish_contract`` — stamps the contract as ACTIVE in the catalog
   and records lineage.

Connection required
~~~~~~~~~~~~~~~~~~~
A ``data_contract_yaml`` connection must be configured so the hook can
find the contract file.  See the README for setup instructions.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from airflow.providers.data.contracts.operators.contract_publish import ContractPublishOperator
from airflow.providers.data.contracts.operators.contract_validate import ContractValidateOperator
from airflow.sdk import DAG, task

DATASET_URN = "urn:li:dataset:(urn:li:dataPlatform:postgres,warehouse.daily_orders,PROD)"
CONTRACT_YAML = os.path.join(os.path.dirname(os.path.dirname(__file__)), "contracts", "daily_orders.yaml")
CATALOG_CONN_ID = "data_contract_yaml_default"


@task
def load_orders() -> dict:
    """Simulate an ETL step that produces output statistics."""
    return {
        "row_count": 42,
        "schema": [
            {"name": "order_id", "type": "STRING", "nullable": False},
            {"name": "customer_id", "type": "STRING", "nullable": False},
            {"name": "order_date", "type": "STRING", "nullable": False},
            {"name": "amount", "type": "FLOAT", "nullable": False},
            {"name": "status", "type": "STRING", "nullable": True},
        ],
        "data_as_of": datetime.now(timezone.utc).isoformat(),
    }


with DAG(
    dag_id="aip07_producer",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["aip-07", "data-contracts", "producer"],
    doc_md=__doc__,
) as dag:
    stats = load_orders()

    validate = ContractValidateOperator(
        task_id="validate_contract",
        catalog_conn_id=CATALOG_CONN_ID,
        dataset_urn=DATASET_URN,
        stats_xcom_task_id="load_orders",
        contract_yaml_path=CONTRACT_YAML,
        validate_freshness=True,
        validate_completeness=True,
        validate_schema=True,
        validate_sla=False,
        on_schema_violation="fail",
        on_freshness_violation="warn",
        on_completeness_violation="fail",
        report_breach_to_catalog=False,
    )

    publish = ContractPublishOperator(
        task_id="publish_contract",
        catalog_conn_id=CATALOG_CONN_ID,
        dataset_urn=DATASET_URN,
        stats_xcom_task_id="load_orders",
    )

    stats >> validate >> publish
