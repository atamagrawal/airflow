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
Minimal data-contract DAG using **TaskFlow decorators** (collect stats + validate).

Same scenario as ``example/aip-07/minimal_standalone/dags/simple_data_contract.py``.

This DAG uses the stacked style: plain ``@task`` outer with inner ``@contract_validate``.

Requires:

* ``apache-airflow-providers-data-contracts``
* ``apache-airflow-providers-data-contracts-decorators``

The contract is resolved from the platform-managed catalog mapping for ``dataset_urn``.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.data.contracts_decorators.decorators.contract_validate import (
    contract_validate,
)
from airflow.sdk import DAG, task

DATASET_URN = "urn:example:sample_dataset"


def _sample_stats() -> dict:
    return {
        "row_count": 3,
        "schema": [
            {"name": "id", "type": "STRING", "nullable": False},
            {"name": "amount", "type": "FLOAT", "nullable": False},
        ],
    }


@task
@contract_validate(
    dataset_urn=DATASET_URN,
    validate_schema=True,
    validate_completeness=True,
    validate_freshness=False,
    validate_sla=False,
    report_breach_to_catalog=False,
)
def validate_sample_dataset() -> dict:
    """Return contract stats for validation under ``@task`` + ``contract_validate``."""
    return _sample_stats()


with DAG(
    dag_id="simple_data_contract_validation_decorators",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal", "decorators"],
    doc_md=__doc__,
) as _:
    validate_sample_dataset()
