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
"""Shared contract validation logic for :class:`ContractValidateOperator` and task decorators."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Protocol

from airflow.providers.common.compat.sdk import AirflowException
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


class _XComPushPull(Protocol):
    def xcom_push(self, key: str, value: object) -> None: ...


def load_contract_for_validation(
    *,
    catalog_conn_id: str,
    dataset_urn: str,
    contract_yaml_path: str | None,
) -> DataContract:
    """Load a :class:`~airflow.providers.data.contracts.models.contract.DataContract` from YAML or catalog."""
    if contract_yaml_path:
        return YamlDataContractHook.load_contract_from_file(contract_yaml_path)
    hook = get_catalog_hook(catalog_conn_id=catalog_conn_id)
    return hook.get_contract(dataset_urn)


def severity_for_violation(
    violation: ContractViolation,
    *,
    on_schema_violation: OnViolation,
    on_freshness_violation: OnViolation,
    on_completeness_violation: OnViolation,
    on_sla_violation: OnViolation,
) -> str:
    if violation.violation_type == "SCHEMA_MISMATCH":
        policy = on_schema_violation
    elif violation.violation_type == "FRESHNESS":
        policy = on_freshness_violation
    elif violation.violation_type == "COMPLETENESS":
        policy = on_completeness_violation
    elif violation.violation_type == "SLA":
        policy = on_sla_violation
    else:
        policy = "fail"
    if policy == "warn":
        return "WARNING"
    return "CRITICAL"


def validate_contract_stats(
    *,
    stats: dict,
    context: Context,
    task_id: str,
    ti: _XComPushPull,
    catalog_conn_id: str,
    dataset_urn: str,
    contract_yaml_path: str | None = None,
    validate_schema_flag: bool = True,
    validate_freshness_flag: bool = True,
    validate_completeness_flag: bool = True,
    validate_sla_flag: bool = True,
    on_schema_violation: OnViolation = "fail",
    on_freshness_violation: OnViolation = "warn",
    on_completeness_violation: OnViolation = "fail",
    on_sla_violation: OnViolation = "warn",
    report_breach_to_catalog: bool = True,
    result_xcom_key: str = "contract_result",
) -> dict:
    """
    Run validators, optionally report breaches, push the result to XCom, and fail on critical violations.

    Used by :class:`~airflow.providers.data.contracts.operators.contract_validate.ContractValidateOperator`
    and :func:`~airflow.providers.data.contracts_decorators.decorators.contract_validate.contract_validate_task`.
    """
    contract = load_contract_for_validation(
        catalog_conn_id=catalog_conn_id,
        dataset_urn=dataset_urn,
        contract_yaml_path=contract_yaml_path,
    )
    violations: list[ContractViolation] = []

    if validate_schema_flag:
        violations.extend(validate_schema(contract, stats))
    if validate_completeness_flag:
        violations.extend(validate_completeness(contract, stats))
    if validate_freshness_flag:
        violations.extend(validate_freshness(contract, stats))

    dr = context.get("dag_run")
    start = dr.start_date if dr else None
    end = dr.end_date if dr else None
    if validate_sla_flag:
        violations.extend(
            validate_sla(
                contract,
                dag_run_start=start,
                dag_run_end=end,
            )
        )

    adjusted: list[ContractViolation] = []
    for v in violations:
        sev = severity_for_violation(
            v,
            on_schema_violation=on_schema_violation,
            on_freshness_violation=on_freshness_violation,
            on_completeness_violation=on_completeness_violation,
            on_sla_violation=on_sla_violation,
        )
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
    if critical and report_breach_to_catalog:
        hook = get_catalog_hook(catalog_conn_id=catalog_conn_id)
        breach_id = hook.report_breach(
            dataset_urn,
            critical,
            dag_id=context["dag"].dag_id,
            run_id=context["run_id"],
        )

    schema_diff = compute_schema_diff(contract, stats) if validate_schema_flag else None
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
        task_id=task_id,
        actual_row_count=int(stats["row_count"]) if "row_count" in stats else None,
        actual_schema_version=int(stats["schema_version"]) if "schema_version" in stats else None,
        data_freshness_minutes=data_freshness_minutes,
        run_duration_minutes=run_duration_minutes,
        schema_diff=schema_diff,
        breach_reported_to_catalog=bool(critical and report_breach_to_catalog),
        breach_id=breach_id,
    )

    payload = result.to_xcom_dict()
    ti.xcom_push(key=result_xcom_key, value=payload)

    if critical:
        raise AirflowException("Contract validation failed: " + "; ".join(c.message for c in critical))
    return payload
