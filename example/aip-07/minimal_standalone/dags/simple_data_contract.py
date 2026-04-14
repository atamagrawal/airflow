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
Minimal data-contract DAG (no Postgres, DataHub, or extra Airflow connections).

Copy this folder into your ``dags/`` tree (keep ``contracts/`` next to ``dags/``),
or set ``CONTRACT_YAML`` to an absolute path.

Requires: ``apache-airflow-providers-data-contracts`` (and Airflow with Task SDK).

See also: ``example/aip-07/minimal_decorators/`` for the same scenario with TaskFlow decorators.

``catalog_conn_id`` is unused when ``contract_yaml_path`` is set and
``report_breach_to_catalog`` is False; a placeholder satisfies the operator API.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.providers.data.contracts.operators.contract_validate import ContractValidateOperator
from airflow.sdk import DAG, task

_EXAMPLE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT_YAML = os.path.join(_EXAMPLE_ROOT, "contracts", "sample_dataset.yaml")
DATASET_URN = "urn:example:sample_dataset"


@task
def build_contract_stats() -> dict:
    """
    Simulate pipeline output stats (what you would XCom from a load/transform task).

    Keys used by :class:`~airflow.providers.data.contracts.operators.contract_validate.ContractValidateOperator`:
    ``row_count``, ``schema`` (list of ``name`` / ``type`` / ``nullable``).
    """
    return {
        "row_count": 3,
        "schema": [
            {"name": "id", "type": "STRING", "nullable": False},
            {"name": "amount", "type": "FLOAT", "nullable": False},
        ],
    }


with DAG(
    dag_id="simple_data_contract_validation",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal"],
    doc_md=__doc__,
) as dag:
    stats = build_contract_stats()

    validate = ContractValidateOperator(
        task_id="validate_contract",
        catalog_conn_id="unused_local_yaml_only",
        dataset_urn=DATASET_URN,
        stats_xcom_task_id="build_contract_stats",
        contract_yaml_path=CONTRACT_YAML,
        validate_schema=True,
        validate_completeness=True,
        validate_freshness=False,
        validate_sla=False,
        report_breach_to_catalog=False,
    )

    stats >> validate
