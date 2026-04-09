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

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from airflow.providers.common.compat.sdk import AirflowException
from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook
from airflow.providers.data.contracts.operators.contract_trigger_user_guard import (
    ContractTriggerUserGuardOperator,
)


def test_trigger_user_guard_allows_missing_when_configured():
    op = ContractTriggerUserGuardOperator(
        task_id="t",
        allowed_users=["alice"],
        when_triggering_user_missing="allow",
    )
    dr = MagicMock()
    dr.triggering_user_name = None
    dag = MagicMock()
    dag.dag_id = "d1"
    op.execute({"dag_run": dr, "dag": dag})


def test_trigger_user_guard_rejects_wrong_user():
    op = ContractTriggerUserGuardOperator(task_id="t", allowed_users=["alice"])
    dr = MagicMock()
    dr.triggering_user_name = "bob"
    dag = MagicMock()
    dag.dag_id = "d1"
    with pytest.raises(AirflowException):
        op.execute({"dag_run": dr, "dag": dag})


def test_trigger_user_guard_accepts_allowed_user():
    op = ContractTriggerUserGuardOperator(task_id="t", allowed_users=["alice"])
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    op.execute({"dag_run": dr, "dag": dag})


def test_trigger_user_guard_requires_config():
    with pytest.raises(ValueError, match="dataset_urn is required"):
        ContractTriggerUserGuardOperator(task_id="t")
    with pytest.raises(ValueError, match="dataset_urn is required"):
        ContractTriggerUserGuardOperator(task_id="t", allowed_users=[])
    with pytest.raises(ValueError, match="cannot be combined"):
        ContractTriggerUserGuardOperator(
            task_id="t",
            allowed_users=["alice"],
            dataset_urn="urn:x",
        )
    with pytest.raises(TypeError, match="Invalid arguments"):
        ContractTriggerUserGuardOperator(task_id="t", contract_yaml_path="/tmp/x.yaml")
    with pytest.raises(TypeError, match="Invalid arguments"):
        ContractTriggerUserGuardOperator(task_id="t", catalog_conn_id="any")


@patch("airflow.providers.data.contracts.contract_validate_runner.load_contract_for_validation")
def test_trigger_user_guard_dataset_urn_only_uses_default_catalog_conn(mock_load):
    mock_contract = MagicMock()
    mock_contract.allowed_trigger_users = ["alice"]
    mock_load.return_value = mock_contract

    op = ContractTriggerUserGuardOperator(task_id="t", dataset_urn="urn:test:t")
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    op.execute({"dag_run": dr, "dag": dag})

    mock_load.assert_called_once()
    assert mock_load.call_args.kwargs["catalog_conn_id"] == YamlDataContractHook.default_conn_name
    assert mock_load.call_args.kwargs["contract_yaml_path"] is None
    assert mock_load.call_args.kwargs["dataset_urn"] == "urn:test:t"


@patch("airflow.providers.data.contracts.contract_validate_runner.load_contract_for_validation")
def test_trigger_user_guard_contract_missing_allowed_list_raises(mock_load):
    mock_contract = MagicMock()
    mock_contract.allowed_trigger_users = None
    mock_load.return_value = mock_contract
    op = ContractTriggerUserGuardOperator(task_id="t", dataset_urn="urn:test:t")
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    with pytest.raises(ValueError, match="allowed_trigger_users"):
        op.execute({"dag_run": dr, "dag": dag})


def test_trigger_user_guard_pause_sets_flag():
    op = ContractTriggerUserGuardOperator(
        task_id="t",
        allowed_users=["alice"],
        on_unauthorized="pause_dag",
    )
    dr = MagicMock()
    dr.triggering_user_name = "bob"
    dag = MagicMock()
    dag.dag_id = "d1"
    dm = MagicMock()
    session = MagicMock()
    session.scalar.return_value = dm

    @contextmanager
    def _fake_session():
        yield session

    with patch(
        "airflow.utils.session.create_session",
        return_value=_fake_session(),
    ):
        with pytest.raises(AirflowException):
            op.execute({"dag_run": dr, "dag": dag})
    assert dm.is_paused is True
