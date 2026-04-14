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
Stub **producer publish** using TaskFlow contract helpers.

Publishing always goes through the catalog hook, so you must define a
``data_contract_yaml`` connection that maps ``DATASET_URN`` to
``contracts/sample_dataset.yaml``. See ``README.md`` in this folder.

This DAG uses the stacked style: plain ``@task`` outer with inner ``@contract_publish``.
"""

from __future__ import annotations

from datetime import datetime

from airflow.providers.data.contracts_decorators.decorators.contract_publish import (
    contract_publish,
)
from airflow.sdk import DAG, task

DATASET_URN = "urn:example:sample_dataset"


def _publish_stats_payload() -> dict:
    return {
        "row_count": 3,
        "schema": [
            {"name": "id", "type": "STRING", "nullable": False},
            {"name": "amount", "type": "FLOAT", "nullable": False},
        ],
    }


@task
@contract_publish(
    dataset_urn=DATASET_URN,
    upstream_urns=[],
    update_contract_status=True,
    contract_status="ACTIVE",
    emit_run_facet=True,
)
def publish_sample_dataset_stats() -> dict:
    """Return publish stats via ``@task`` + ``contract_publish``."""
    return _publish_stats_payload()


with DAG(
    dag_id="simple_data_contract_publish_decorators",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal", "decorators"],
    doc_md=__doc__,
) as _:
    publish_sample_dataset_stats()
