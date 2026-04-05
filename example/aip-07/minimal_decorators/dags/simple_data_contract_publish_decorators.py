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
Stub **producer publish** using ``contract_publish_task``.

Publishing always goes through the catalog hook, so you must define a
``data_contract_yaml`` connection that maps ``DATASET_URN`` to
``contracts/sample_dataset.yaml``. See ``README.md`` in this folder.
"""

from __future__ import annotations

import os
from datetime import datetime

from airflow.providers.data.contracts_decorators.decorators.contract_publish import (
    contract_publish_task,
)
from airflow.sdk import DAG

_EXAMPLE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_URN = "urn:example:sample_dataset"
CATALOG_CONN_ID = os.environ.get("AIP07_YAML_CATALOG_CONN_ID", "data_contract_yaml_default")


@contract_publish_task(
    catalog_conn_id=CATALOG_CONN_ID,
    dataset_urn=DATASET_URN,
    upstream_urns=[],
    update_contract_status=True,
    contract_status="ACTIVE",
    emit_run_facet=True,
    task_id="publish_contract",
)
def publish_sample_dataset_stats() -> dict:
    """Stats passed to ``update_contract_status`` (illustrative — not tied to a real load)."""
    return {
        "row_count": 3,
        "schema": [
            {"name": "id", "type": "STRING", "nullable": False},
            {"name": "amount", "type": "FLOAT", "nullable": False},
        ],
    }


with DAG(
    dag_id="simple_data_contract_publish_decorators",
    schedule=None,
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["data-contracts", "example", "minimal", "decorators"],
    doc_md=__doc__,
) as _:
    publish_sample_dataset_stats()
