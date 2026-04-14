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

import pytest

from airflow.providers.common.compat.sdk import AirflowException
from airflow.providers.data.contracts.models.contract import DataContract, SchemaField
from airflow.providers.data.contracts_decorators.decorators.contract_ready import (
    _ContractReadyDecoratedSensor,
    contract_ready,
)


def test_contract_ready_decorated_sensor_poke_true():
    def urn():
        return "urn:ds"

    op = _ContractReadyDecoratedSensor(
        task_id="ready",
        python_callable=urn,
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


def test_contract_ready_stackable_returns_urn_when_ready():
    @contract_ready()
    def urn():
        return "urn:ds"

    mock_task = MagicMock()
    mock_task.render_template.side_effect = lambda v, *a, **k: v
    mock_task.get_template_env.return_value = MagicMock()
    mock_task.task_id = "ready"
    mock_task.log = MagicMock()
    context = {"task": mock_task, "ti": MagicMock(), "dag": MagicMock(), "run_id": "r1", "dag_run": None}

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
    with (
        patch(
            "airflow.providers.data.contracts.contract_ready_runner.get_catalog_hook",
            return_value=mock_hook,
        ),
        patch("airflow.sdk.get_current_context", return_value=context),
    ):
        assert urn() == "urn:ds"


def test_contract_ready_stackable_fails_when_not_ready():
    @contract_ready(min_update_time="2099-01-01T00:00:00+00:00")
    def urn():
        return "urn:ds"

    mock_task = MagicMock()
    mock_task.render_template.side_effect = lambda v, *a, **k: v
    mock_task.get_template_env.return_value = MagicMock()
    mock_task.task_id = "ready"
    mock_task.log = MagicMock()
    context = {"task": mock_task, "ti": MagicMock(), "dag": MagicMock(), "run_id": "r1", "dag_run": None}

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
    with (
        patch(
            "airflow.providers.data.contracts.contract_ready_runner.get_catalog_hook",
            return_value=mock_hook,
        ),
        patch("airflow.sdk.get_current_context", return_value=context),
    ):
        with pytest.raises(AirflowException, match="not ready"):
            urn()
