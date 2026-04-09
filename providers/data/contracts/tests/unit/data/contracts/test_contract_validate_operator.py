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
from airflow.providers.data.contracts.models.contract import DataContract, SchemaField
from airflow.providers.data.contracts.operators.contract_validate import ContractValidateOperator


@pytest.fixture
def sample_contract() -> DataContract:
    return DataContract(
        contract_id="c",
        dataset_urn="urn:x",
        dataset_name="x",
        version=1,
        status="ACTIVE",
        schema=[SchemaField(name="id", type="STRING", nullable=False)],
        min_row_count=1,
        freshness_max_age_minutes=None,
        sla_completion_minutes=None,
    )


def test_contract_validate_operator_success(sample_contract):
    op = ContractValidateOperator(
        task_id="validate",
        catalog_conn_id="datahub_default",
        dataset_urn="urn:x",
        stats_xcom_task_id="load",
        validate_freshness=False,
        validate_sla=False,
        report_breach_to_catalog=False,
    )
    ti = MagicMock()
    ti.xcom_pull.return_value = {
        "row_count": 5,
        "schema": [{"name": "id", "type": "STRING", "nullable": False}],
    }
    dag = MagicMock()
    dag.dag_id = "dag1"
    context = {"ti": ti, "dag": dag, "run_id": "run1", "dag_run": None}

    mock_hook = MagicMock()
    mock_hook.get_contract.return_value = sample_contract
    mock_hook.report_breach.return_value = None

    with patch(
        "airflow.providers.data.contracts.contract_validate_runner.get_catalog_hook",
        return_value=mock_hook,
    ):
        out = op.execute(context)
    assert out["passed"] is True
    ti.xcom_push.assert_called_once()


def test_contract_validate_operator_requires_catalog_or_yaml():
    with pytest.raises(ValueError, match="catalog_conn_id or contract_yaml_path"):
        ContractValidateOperator(
            task_id="validate",
            dataset_urn="urn:x",
            stats_xcom_task_id="load",
        )


def test_contract_validate_operator_yaml_only_no_connection(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "dataset_urn: urn:local:x\n"
        "dataset_name: x\nversion: 1\nstatus: ACTIVE\nschema:\n"
        "  - name: id\n    type: STRING\n    nullable: false\n"
        "min_row_count: 1\n",
        encoding="utf-8",
    )
    op = ContractValidateOperator(
        task_id="validate",
        dataset_urn="urn:local:x",
        contract_yaml_path=str(p),
        stats_xcom_task_id="load",
        validate_freshness=False,
        validate_sla=False,
        report_breach_to_catalog=False,
    )
    ti = MagicMock()
    ti.xcom_pull.return_value = {
        "row_count": 5,
        "schema": [{"name": "id", "type": "STRING", "nullable": False}],
    }
    dag = MagicMock()
    dag.dag_id = "dag1"
    context = {"ti": ti, "dag": dag, "run_id": "run1", "dag_run": None}
    out = op.execute(context)
    assert out["passed"] is True
    ti.xcom_push.assert_called_once()


def test_contract_validate_operator_fails_on_schema(sample_contract):
    op = ContractValidateOperator(
        task_id="validate",
        catalog_conn_id="datahub_default",
        dataset_urn="urn:x",
        stats_xcom_task_id="load",
        validate_freshness=False,
        validate_sla=False,
        report_breach_to_catalog=False,
    )
    ti = MagicMock()
    ti.xcom_pull.return_value = {"row_count": 5, "schema": []}
    dag = MagicMock()
    dag.dag_id = "dag1"
    context = {"ti": ti, "dag": dag, "run_id": "run1", "dag_run": None}
    mock_hook = MagicMock()
    mock_hook.get_contract.return_value = sample_contract
    with patch(
        "airflow.providers.data.contracts.contract_validate_runner.get_catalog_hook",
        return_value=mock_hook,
    ):
        with pytest.raises(AirflowException):
            op.execute(context)
