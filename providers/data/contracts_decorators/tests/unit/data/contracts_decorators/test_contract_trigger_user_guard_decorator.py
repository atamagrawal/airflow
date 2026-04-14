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

from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook
from airflow.providers.data.contracts_decorators.decorators.contract_trigger_user_guard import (
    _ContractTriggerUserGuardDecoratedOperator,
    contract_trigger_user_guard,
)


def test_contract_trigger_user_guard_decorated_accepts_inline_allowlist():
    def body():
        return "ok"

    op = _ContractTriggerUserGuardDecoratedOperator(
        task_id="t",
        python_callable=body,
        allowed_users=["alice"],
        when_triggering_user_missing="allow",
    )
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    assert op.execute({"dag_run": dr, "dag": dag, "ti": MagicMock()}) == "ok"


def test_contract_trigger_user_guard_stackable_runs_guard_then_body():
    mock_task = MagicMock()
    mock_task.render_template.side_effect = lambda v, *a, **k: v
    mock_task.get_template_env.return_value = MagicMock()
    mock_task.log = MagicMock()
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    ctx = {"task": mock_task, "dag": dag, "dag_run": dr}

    @contract_trigger_user_guard(allowed_users=["alice"])
    def inner():
        return 42

    with patch("airflow.sdk.get_current_context", return_value=ctx):
        assert inner() == 42


@patch("airflow.providers.data.contracts.contract_validate_runner.load_contract_for_validation")
def test_contract_trigger_user_guard_stackable_dataset_urn_uses_default_catalog(mock_load):
    mock_contract = MagicMock()
    mock_contract.allowed_trigger_users = ["alice"]
    mock_load.return_value = mock_contract

    mock_task = MagicMock()
    mock_task.render_template.side_effect = lambda v, *a, **k: v
    mock_task.get_template_env.return_value = MagicMock()
    mock_task.log = MagicMock()
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    ctx = {"task": mock_task, "dag": dag, "dag_run": dr}

    @contract_trigger_user_guard(
        dataset_urn="urn:test:t",
        when_triggering_user_missing="allow",
        on_unauthorized="fail",
    )
    def inner():
        return "done"

    with patch("airflow.sdk.get_current_context", return_value=ctx):
        assert inner() == "done"

    mock_load.assert_called_once()
    assert mock_load.call_args.kwargs["catalog_conn_id"] == YamlDataContractHook.default_conn_name
    assert mock_load.call_args.kwargs["contract_yaml_path"] is None
    assert mock_load.call_args.kwargs["dataset_urn"] == "urn:test:t"


def test_contract_trigger_user_guard_invalid_missing_dataset_urn():
    def body():
        return 1

    with pytest.raises(ValueError, match="dataset_urn is required"):
        _ContractTriggerUserGuardDecoratedOperator(
            task_id="t",
            python_callable=body,
        )


def test_contract_trigger_user_guard_rejects_catalog_overrides():
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        contract_trigger_user_guard(dataset_urn="urn:x", contract_yaml_path="/tmp/x.yaml")
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        contract_trigger_user_guard(dataset_urn="urn:x", catalog_conn_id="foo")
