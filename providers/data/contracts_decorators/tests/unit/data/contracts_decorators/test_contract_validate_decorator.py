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
from airflow.providers.data.contracts_decorators.decorators.contract_validate import (
    _ContractValidateDecoratedOperator,
)


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


def test_contract_validate_decorated_operator_success(sample_contract):
    def load():
        return {
            "row_count": 5,
            "schema": [{"name": "id", "type": "STRING", "nullable": False}],
        }

    op = _ContractValidateDecoratedOperator(
        task_id="validate",
        python_callable=load,
        catalog_conn_id="datahub_default",
        dataset_urn="urn:x",
        validate_freshness=False,
        validate_sla=False,
        report_breach_to_catalog=False,
    )
    ti = MagicMock()
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


def test_contract_validate_decorated_operator_fails_on_schema(sample_contract):
    def load():
        return {"row_count": 5, "schema": []}

    op = _ContractValidateDecoratedOperator(
        task_id="validate",
        python_callable=load,
        catalog_conn_id="datahub_default",
        dataset_urn="urn:x",
        validate_freshness=False,
        validate_sla=False,
        report_breach_to_catalog=False,
    )
    ti = MagicMock()
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
