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

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

from airflow.providers.common.compat.sdk import AirflowException, BaseOperator
from airflow.providers.data.contracts.hooks.base_catalog import get_catalog_hook
from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook
from airflow.providers.data.contracts.models.contract import ContractViolation, DataContract
from airflow.providers.data.contracts.models.contract_result import build_contract_result
from airflow.providers.data.contracts.validators.contract_validators import (
    compute_schema_diff,
    validate_completeness,
    validate_freshness,
    validate_schema,
    validate_sla,
)

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context

OnViolation = Literal["fail", "warn"]


class ContractValidateOperator(BaseOperator):
    """
    Fetch a data contract and validate task output statistics from XCom.

    Stats mapping (pulled from XCom) commonly includes:

    * ``row_count`` — int
    * ``schema`` — list of dicts with ``name``, ``type``, ``nullable``
    * ``data_as_of`` — ISO-8601 timestamp for freshness checks
    """

    template_fields: Sequence[str] = (
        "dataset_urn",
        "contract_yaml_path",
        "stats_xcom_task_id",
        "stats_xcom_key",
    )

    def __init__(
        self,
        *,
        catalog_conn_id: str,
        dataset_urn: str,
        stats_xcom_task_id: str,
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

    def _load_contract(self) -> DataContract:
        if self.contract_yaml_path:
            return YamlDataContractHook.load_contract_from_file(self.contract_yaml_path)
        hook = get_catalog_hook(catalog_conn_id=self.catalog_conn_id)
        return hook.get_contract(self.dataset_urn)

    def _severity_for(self, v: ContractViolation) -> str:
        if v.violation_type == "SCHEMA_MISMATCH":
            policy = self.on_schema_violation
        elif v.violation_type == "FRESHNESS":
            policy = self.on_freshness_violation
        elif v.violation_type == "COMPLETENESS":
            policy = self.on_completeness_violation
        elif v.violation_type == "SLA":
            policy = self.on_sla_violation
        else:
            policy = "fail"
        if policy == "warn":
            return "WARNING"
        return "CRITICAL"

    def execute(self, context: Context) -> dict:
        ti = context["ti"]
        stats = ti.xcom_pull(task_ids=self.stats_xcom_task_id, key=self.stats_xcom_key)
        if not isinstance(stats, dict):
            msg = f"Stats from XCom must be a dict, got {type(stats).__name__}"
            raise TypeError(msg)

        contract = self._load_contract()
        violations: list[ContractViolation] = []

        if self.validate_schema:
            violations.extend(validate_schema(contract, stats))
        if self.validate_completeness:
            violations.extend(validate_completeness(contract, stats))
        if self.validate_freshness:
            violations.extend(validate_freshness(contract, stats))

        dr = context.get("dag_run")
        start = dr.start_date if dr else None
        end = dr.end_date if dr else None
        if self.validate_sla_flag:
            violations.extend(
                validate_sla(
                    contract,
                    dag_run_start=start,
                    dag_run_end=end,
                )
            )

        adjusted: list[ContractViolation] = []
        for v in violations:
            sev = self._severity_for(v)
            adjusted.append(
                ContractViolation(
                    violation_type=v.violation_type,
                    severity=sev,
                    expected=v.expected,
                    actual=v.actual,
                    field_name=v.field_name,
                    message=v.message,
                )
            )

        critical = [v for v in adjusted if v.severity == "CRITICAL"]
        breach_id: str | None = None
        if critical and self.report_breach_to_catalog:
            hook = get_catalog_hook(catalog_conn_id=self.catalog_conn_id)
            breach_id = hook.report_breach(
                self.dataset_urn,
                critical,
                dag_id=context["dag"].dag_id,
                run_id=context["run_id"],
            )

        schema_diff = compute_schema_diff(contract, stats) if self.validate_schema else None
        data_as_of = stats.get("data_as_of")
        data_freshness_minutes: float | None = None
        if data_as_of:
            try:
                text = str(data_as_of)
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                dt = datetime.fromisoformat(text)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                data_freshness_minutes = (datetime.now(timezone.utc) - dt).total_seconds() / 60.0
            except ValueError:
                data_freshness_minutes = None

        run_duration_minutes: float | None = None
        if start:
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            end_ts = end or datetime.now(timezone.utc)
            if end_ts.tzinfo is None:
                end_ts = end_ts.replace(tzinfo=timezone.utc)
            run_duration_minutes = (end_ts - start).total_seconds() / 60.0

        result = build_contract_result(
            contract=contract,
            violations=adjusted,
            dag_id=context["dag"].dag_id,
            run_id=context["run_id"],
            task_id=self.task_id,
            actual_row_count=int(stats["row_count"]) if "row_count" in stats else None,
            actual_schema_version=int(stats["schema_version"]) if "schema_version" in stats else None,
            data_freshness_minutes=data_freshness_minutes,
            run_duration_minutes=run_duration_minutes,
            schema_diff=schema_diff,
            breach_reported_to_catalog=bool(critical and self.report_breach_to_catalog),
            breach_id=breach_id,
        )

        payload = result.to_xcom_dict()
        ti.xcom_push(key=self.result_xcom_key, value=payload)

        if critical:
            raise AirflowException("Contract validation failed: " + "; ".join(c.message for c in critical))
        return payload
