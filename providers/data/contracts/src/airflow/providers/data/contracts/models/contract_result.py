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

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from airflow.providers.common.compat.sdk import AirflowException
from airflow.providers.data.contracts.models.contract import ContractViolation, DataContract
from airflow.providers.data.contracts.models.schema_diff import SchemaDiff


def _dt_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _violation_to_dict(v: ContractViolation) -> dict[str, Any]:
    return {
        "violation_type": v.violation_type,
        "severity": v.severity,
        "expected": v.expected,
        "actual": v.actual,
        "field_name": v.field_name,
        "message": v.message,
    }


def _violation_from_dict(d: dict[str, Any]) -> ContractViolation:
    return ContractViolation(
        violation_type=d["violation_type"],
        severity=d["severity"],
        expected=d["expected"],
        actual=d["actual"],
        field_name=d.get("field_name"),
        message=d["message"],
    )


@dataclass
class ContractResult:
    """Serializable outcome of a contract validation run (for XCom and auditing)."""

    dataset_urn: str
    dataset_name: str
    contract_version: int
    catalog_url: str
    passed: bool
    violations: list[ContractViolation]
    critical_violations: list[ContractViolation]
    warning_violations: list[ContractViolation]
    actual_row_count: int | None
    actual_schema_version: int | None
    data_freshness_minutes: float | None
    run_duration_minutes: float | None
    schema_diff: SchemaDiff | None
    breach_reported_to_catalog: bool
    breach_id: str | None
    dag_id: str
    run_id: str
    task_id: str
    validated_at: datetime

    def raise_if_failed(self) -> None:
        if not self.passed:
            msg = "; ".join(v.message for v in self.critical_violations) or "Contract validation failed"
            raise AirflowException(msg)

    def to_xcom_dict(self) -> dict[str, Any]:
        return {
            "dataset_urn": self.dataset_urn,
            "dataset_name": self.dataset_name,
            "contract_version": self.contract_version,
            "catalog_url": self.catalog_url,
            "passed": self.passed,
            "violations": [_violation_to_dict(v) for v in self.violations],
            "critical_violations": [_violation_to_dict(v) for v in self.critical_violations],
            "warning_violations": [_violation_to_dict(v) for v in self.warning_violations],
            "actual_row_count": self.actual_row_count,
            "actual_schema_version": self.actual_schema_version,
            "data_freshness_minutes": self.data_freshness_minutes,
            "run_duration_minutes": self.run_duration_minutes,
            "schema_diff": asdict(self.schema_diff) if self.schema_diff else None,
            "breach_reported_to_catalog": self.breach_reported_to_catalog,
            "breach_id": self.breach_id,
            "dag_id": self.dag_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "validated_at": _dt_iso(self.validated_at),
        }

    @classmethod
    def from_xcom_dict(cls, payload: dict[str, Any]) -> ContractResult:
        def _violations(key: str) -> list[ContractViolation]:
            return [_violation_from_dict(x) for x in payload.get(key) or []]

        validated_at = payload["validated_at"]
        if isinstance(validated_at, str):
            va = datetime.fromisoformat(validated_at.replace("Z", "+00:00"))
        else:
            va = validated_at

        sd = payload.get("schema_diff")
        schema_diff = SchemaDiff(**sd) if sd else None

        return cls(
            dataset_urn=payload["dataset_urn"],
            dataset_name=payload["dataset_name"],
            contract_version=payload["contract_version"],
            catalog_url=payload.get("catalog_url", ""),
            passed=payload["passed"],
            violations=_violations("violations"),
            critical_violations=_violations("critical_violations"),
            warning_violations=_violations("warning_violations"),
            actual_row_count=payload.get("actual_row_count"),
            actual_schema_version=payload.get("actual_schema_version"),
            data_freshness_minutes=payload.get("data_freshness_minutes"),
            run_duration_minutes=payload.get("run_duration_minutes"),
            schema_diff=schema_diff,
            breach_reported_to_catalog=payload.get("breach_reported_to_catalog", False),
            breach_id=payload.get("breach_id"),
            dag_id=payload["dag_id"],
            run_id=payload["run_id"],
            task_id=payload["task_id"],
            validated_at=va,
        )


def build_contract_result(
    *,
    contract: DataContract,
    violations: list[ContractViolation],
    dag_id: str,
    run_id: str,
    task_id: str,
    actual_row_count: int | None,
    actual_schema_version: int | None,
    data_freshness_minutes: float | None,
    run_duration_minutes: float | None,
    schema_diff: SchemaDiff | None,
    breach_reported_to_catalog: bool,
    breach_id: str | None,
) -> ContractResult:
    critical = [v for v in violations if v.severity == "CRITICAL"]
    warning = [v for v in violations if v.severity == "WARNING"]
    return ContractResult(
        dataset_urn=contract.dataset_urn,
        dataset_name=contract.dataset_name,
        contract_version=contract.version,
        catalog_url=contract.catalog_url,
        passed=not critical,
        violations=violations,
        critical_violations=critical,
        warning_violations=warning,
        actual_row_count=actual_row_count,
        actual_schema_version=actual_schema_version,
        data_freshness_minutes=data_freshness_minutes,
        run_duration_minutes=run_duration_minutes,
        schema_diff=schema_diff,
        breach_reported_to_catalog=breach_reported_to_catalog,
        breach_id=breach_id,
        dag_id=dag_id,
        run_id=run_id,
        task_id=task_id,
        validated_at=datetime.now(timezone.utc),
    )
