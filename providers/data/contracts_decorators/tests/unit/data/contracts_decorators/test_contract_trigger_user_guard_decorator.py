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

from airflow.providers.data.contracts_decorators.decorators.contract_trigger_user_guard import (
    _ContractTriggerUserGuardDecoratedOperator,
)


def test_contract_trigger_user_guard_decorated_accepts():
    def allowed():
        return ["alice"]

    op = _ContractTriggerUserGuardDecoratedOperator(
        task_id="t",
        python_callable=allowed,
        when_triggering_user_missing="allow",
    )
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    op.execute({"dag_run": dr, "dag": dag, "ti": MagicMock()})


def test_contract_trigger_user_guard_yaml_mode_returns_callable_result(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "dataset_urn: urn:t\n"
        "dataset_name: t\nversion: 1\nstatus: ACTIVE\nschema: []\n"
        "allowed_trigger_users:\n  - alice\n",
        encoding="utf-8",
    )

    def body():
        return "done"

    op = _ContractTriggerUserGuardDecoratedOperator(
        task_id="t",
        python_callable=body,
        contract_yaml_path=str(p),
        when_triggering_user_missing="allow",
    )
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    assert op.execute({"dag_run": dr, "dag": dag, "ti": MagicMock()}) == "done"


def test_with_contract_trigger_user_from_yaml_runs_wrapped_fn(tmp_path):
    from airflow.providers.data.contracts_decorators.decorators.with_contract_trigger_user_from_yaml import (
        with_contract_trigger_user_from_yaml,
    )

    p = tmp_path / "c.yaml"
    p.write_text(
        "dataset_urn: urn:t\n"
        "dataset_name: t\nversion: 1\nstatus: ACTIVE\nschema: []\n"
        "allowed_trigger_users:\n  - alice\n",
        encoding="utf-8",
    )

    mock_task = MagicMock()
    mock_task.render_template.side_effect = lambda path, *a, **k: path
    mock_task.get_template_env.return_value = MagicMock()
    mock_task.log = MagicMock()
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    ctx = {"task": mock_task, "dag": dag, "dag_run": dr}

    @with_contract_trigger_user_from_yaml(str(p))
    def inner():
        return 42

    with patch("airflow.sdk.get_current_context", return_value=ctx):
        assert inner() == 42


def test_contract_trigger_user_guard_empty_contract_yaml_path_invalid():
    def body():
        return 1

    with pytest.raises(ValueError, match="contract_yaml_path"):
        _ContractTriggerUserGuardDecoratedOperator(
            task_id="t",
            python_callable=body,
            contract_yaml_path="",
        )
