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
from airflow.providers.data.contracts.operators.contract_trigger_user_guard import (
    ContractTriggerUserGuardOperator,
)


def _minimal_yaml_with_users(extra: str = "") -> str:
    return f"""dataset_urn: urn:test:t
dataset_name: t
version: 1
status: ACTIVE
schema: []
allowed_trigger_users:
  - alice
  - bob
{extra}"""


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


def test_trigger_user_guard_requires_exactly_one_source():
    with pytest.raises(ValueError, match="exactly one"):
        ContractTriggerUserGuardOperator(task_id="t")
    with pytest.raises(ValueError, match="exactly one"):
        ContractTriggerUserGuardOperator(task_id="t", allowed_users=[], contract_yaml_path=None)
    with pytest.raises(ValueError, match="exactly one"):
        ContractTriggerUserGuardOperator(
            task_id="t",
            allowed_users=["alice"],
            contract_yaml_path="/tmp/x.yaml",
        )


def test_trigger_user_guard_from_contract_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(_minimal_yaml_with_users(), encoding="utf-8")
    op = ContractTriggerUserGuardOperator(task_id="t", contract_yaml_path=str(p))
    dr = MagicMock()
    dr.triggering_user_name = "alice"
    dag = MagicMock()
    dag.dag_id = "d1"
    op.execute({"dag_run": dr, "dag": dag})


def test_trigger_user_guard_yaml_missing_allowed_list(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "dataset_urn: urn:test:t\ndataset_name: t\nversion: 1\nstatus: ACTIVE\nschema: []\n",
        encoding="utf-8",
    )
    op = ContractTriggerUserGuardOperator(task_id="t", contract_yaml_path=str(p))
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
