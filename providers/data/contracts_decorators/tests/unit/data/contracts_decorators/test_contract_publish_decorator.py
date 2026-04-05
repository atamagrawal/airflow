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
from __future__ import annotations

from unittest.mock import MagicMock, patch

from airflow.providers.data.contracts_decorators.decorators.contract_publish import (
    _ContractPublishDecoratedOperator,
)


def test_contract_publish_decorated_operator_calls_hook():
    def stats():
        return {"row_count": 1}

    op = _ContractPublishDecoratedOperator(
        task_id="publish",
        python_callable=stats,
        catalog_conn_id="datahub_default",
        dataset_urn="urn:x",
        upstream_urns=["urn:up"],
        update_contract_status=True,
        emit_run_facet=True,
    )
    ti = MagicMock()
    dag = MagicMock()
    dag.dag_id = "dag1"
    context = {"ti": ti, "dag": dag, "run_id": "run1", "dag_run": None}

    mock_hook = MagicMock()
    with patch(
        "airflow.providers.data.contracts.contract_publish_runner.get_catalog_hook",
        return_value=mock_hook,
    ):
        op.execute(context)

    mock_hook.emit_lineage.assert_called_once()
    mock_hook.update_contract_status.assert_called_once()
