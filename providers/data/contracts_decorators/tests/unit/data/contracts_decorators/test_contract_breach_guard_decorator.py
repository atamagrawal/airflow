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

import pytest

from airflow.providers.common.compat.sdk import AirflowException
from airflow.providers.data.contracts_decorators.decorators.contract_breach_guard import (
    _ContractBreachGuardDecoratedOperator,
    contract_breach_guard,
)


def test_contract_breach_guard_decorated_operator_passes():
    def urns():
        return ["urn:a"]

    op = _ContractBreachGuardDecoratedOperator(
        task_id="guard",
        python_callable=urns,
        on_breach="fail",
    )
    context = {"ti": MagicMock(), "dag": MagicMock(), "run_id": "r1", "dag_run": None}
    context["dag"].dag_id = "d1"

    mock_hook = MagicMock()
    mock_hook.get_contract_status.return_value = "ACTIVE"
    with (
        patch(
            "airflow.providers.data.contracts.contract_breach_runner.get_catalog_hook",
            return_value=mock_hook,
        ),
        patch("airflow.providers.data.contracts.contract_breach_runner.Variable.get", return_value="0"),
    ):
        op.execute(context)


def test_contract_breach_guard_decorated_operator_fails():
    def urns():
        return ["urn:a"]

    op = _ContractBreachGuardDecoratedOperator(
        task_id="guard",
        python_callable=urns,
        on_breach="fail",
    )
    context = {"ti": MagicMock(), "dag": MagicMock(), "run_id": "r1", "dag_run": None}
    context["dag"].dag_id = "d1"

    mock_hook = MagicMock()
    mock_hook.get_contract_status.return_value = "BREACHED"
    with (
        patch(
            "airflow.providers.data.contracts.contract_breach_runner.get_catalog_hook",
            return_value=mock_hook,
        ),
        patch("airflow.providers.data.contracts.contract_breach_runner.Variable.get", return_value="0"),
    ):
        with pytest.raises(AirflowException):
            op.execute(context)


def test_contract_breach_guard_stackable_passes():
    @contract_breach_guard(
        on_breach="fail",
    )
    def urns():
        return ["urn:a"]

    mock_task = MagicMock()
    mock_task.render_template.side_effect = lambda v, *a, **k: v
    mock_task.get_template_env.return_value = MagicMock()
    mock_task.task_id = "guard"
    mock_task.log = MagicMock()
    ctx = {
        "task": mock_task,
        "ti": MagicMock(),
        "dag": MagicMock(),
        "run_id": "r1",
        "dag_run": None,
    }
    ctx["dag"].dag_id = "d1"

    mock_hook = MagicMock()
    mock_hook.get_contract_status.return_value = "ACTIVE"
    with (
        patch(
            "airflow.providers.data.contracts.contract_breach_runner.get_catalog_hook",
            return_value=mock_hook,
        ),
        patch("airflow.providers.data.contracts.contract_breach_runner.Variable.get", return_value="0"),
        patch("airflow.sdk.get_current_context", return_value=ctx),
    ):
        urns()
