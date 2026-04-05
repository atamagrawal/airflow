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

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from airflow.providers.data.contracts.models.contract import DataContract, SchemaField
from airflow.providers.data.contracts_decorators.decorators.contract_ready import (
    _ContractReadyDecoratedSensor,
)


def test_contract_ready_decorated_sensor_poke_true():
    def urn():
        return "urn:ds"

    op = _ContractReadyDecoratedSensor(
        task_id="ready",
        python_callable=urn,
        catalog_conn_id="datahub_default",
        poke_interval=1,
    )
    context = {"ti": MagicMock(), "dag": MagicMock(), "run_id": "r1", "dag_run": None}

    contract = DataContract(
        contract_id="c",
        dataset_urn="urn:ds",
        dataset_name="ds",
        version=1,
        status="ACTIVE",
        schema=[SchemaField(name="id", type="STRING", nullable=False)],
    )
    mock_hook = MagicMock()
    mock_hook.get_contract.return_value = contract
    with patch(
        "airflow.providers.data.contracts.contract_ready_runner.get_catalog_hook",
        return_value=mock_hook,
    ):
        assert op.poke(context) is True


def test_contract_ready_decorated_sensor_poke_waiting():
    def urn():
        return "urn:ds"

    op = _ContractReadyDecoratedSensor(
        task_id="ready",
        python_callable=urn,
        catalog_conn_id="datahub_default",
        min_update_time="2099-01-01T00:00:00+00:00",
        poke_interval=1,
    )
    context = {"ti": MagicMock(), "dag": MagicMock(), "run_id": "r1", "dag_run": None}

    contract = DataContract(
        contract_id="c",
        dataset_urn="urn:ds",
        dataset_name="ds",
        version=1,
        status="ACTIVE",
        schema=[SchemaField(name="id", type="STRING", nullable=False)],
        last_validated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    mock_hook = MagicMock()
    mock_hook.get_contract.return_value = contract
    with patch(
        "airflow.providers.data.contracts.contract_ready_runner.get_catalog_hook",
        return_value=mock_hook,
    ):
        assert op.poke(context) is False
