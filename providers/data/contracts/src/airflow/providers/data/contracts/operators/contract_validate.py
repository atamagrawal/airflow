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
# software distributed under this License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from airflow.providers.common.compat.sdk import BaseOperator
from airflow.providers.data.contracts.contract_validate_runner import (
    require_catalog_conn_or_contract_yaml,
    validate_contract_stats,
)

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context

OnViolation = Literal["fail", "warn"]


class ContractValidateOperator(BaseOperator):
    """
    Fetch a data contract and validate task output statistics from XCom.

    Provide **``catalog_conn_id``** and/or **``contract_yaml_path``**. When the connection is omitted,
    the contract is loaded from the YAML/JSON file via :class:`~airflow.providers.data.contracts.hooks.local_yaml.YamlDataContractHook`
    (``dataset_urn`` must match the file's ``dataset_urn``). When both are set, the file path takes
    precedence for loading.

    Stats mapping (pulled from XCom) commonly includes:

    * ``row_count`` — int
    * ``schema`` — list of dicts with ``name``, ``type``, ``nullable``
    * ``data_as_of`` — ISO-8601 timestamp for freshness checks

    For TaskFlow-style DAGs, install ``apache-airflow-providers-data-contracts-decorators`` and use
    :func:`~airflow.providers.data.contracts_decorators.decorators.contract_validate.contract_validate_task`.
    """

    template_fields: Sequence[str] = (
        "catalog_conn_id",
        "dataset_urn",
        "contract_yaml_path",
        "stats_xcom_task_id",
        "stats_xcom_key",
    )

    def __init__(
        self,
        *,
        dataset_urn: str,
        stats_xcom_task_id: str,
        catalog_conn_id: str | None = None,
        stats_xcom_key: str = "return_value",
        contract_yaml_path: str | None = None,
        validate_schema: bool = True,
        validate_freshness: bool = True,
        validate_completeness: bool = True,
        validate_sla: bool = True,
        on_schema_violation: OnViolation = "fail",
        on_freshness_violation: OnViolation = "warn",
        on_completeness_violation: OnViolation = "fail",
        on_sla_violation: OnViolation = "warn",
        report_breach_to_catalog: bool = True,
        result_xcom_key: str = "contract_result",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        require_catalog_conn_or_contract_yaml(
            catalog_conn_id=catalog_conn_id,
            contract_yaml_path=contract_yaml_path,
        )
        self.catalog_conn_id = catalog_conn_id
        self.dataset_urn = dataset_urn
        self.stats_xcom_task_id = stats_xcom_task_id
        self.stats_xcom_key = stats_xcom_key
        self.contract_yaml_path = contract_yaml_path
        self.validate_schema = validate_schema
        self.validate_freshness = validate_freshness
        self.validate_completeness = validate_completeness
        self.validate_sla_flag = validate_sla
        self.on_schema_violation = on_schema_violation
        self.on_freshness_violation = on_freshness_violation
        self.on_completeness_violation = on_completeness_violation
        self.on_sla_violation = on_sla_violation
        self.report_breach_to_catalog = report_breach_to_catalog
        self.result_xcom_key = result_xcom_key

    def execute(self, context: Context) -> dict:
        ti = context["ti"]
        stats = ti.xcom_pull(task_ids=self.stats_xcom_task_id, key=self.stats_xcom_key)
        if not isinstance(stats, dict):
            msg = f"Stats from XCom must be a dict, got {type(stats).__name__}"
            raise TypeError(msg)

        return validate_contract_stats(
            stats=stats,
            context=context,
            task_id=self.task_id,
            ti=ti,
            catalog_conn_id=self.catalog_conn_id,
            dataset_urn=self.dataset_urn,
            contract_yaml_path=self.contract_yaml_path,
            validate_schema_flag=self.validate_schema,
            validate_freshness_flag=self.validate_freshness,
            validate_completeness_flag=self.validate_completeness,
            validate_sla_flag=self.validate_sla_flag,
            on_schema_violation=self.on_schema_violation,
            on_freshness_violation=self.on_freshness_violation,
            on_completeness_violation=self.on_completeness_violation,
            on_sla_violation=self.on_sla_violation,
            report_breach_to_catalog=self.report_breach_to_catalog,
            result_xcom_key=self.result_xcom_key,
        )
