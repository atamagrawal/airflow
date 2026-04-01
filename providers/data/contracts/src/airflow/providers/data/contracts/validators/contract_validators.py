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

from datetime import datetime, timezone
from typing import Any

from airflow.providers.data.contracts.models.contract import ContractViolation, DataContract, SchemaField
from airflow.providers.data.contracts.models.schema_diff import SchemaDiff


def _parse_data_as_of(stats: dict[str, Any]) -> datetime | None:
    raw = stats.get("data_as_of")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    text = str(raw)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def stats_schema_fields(stats: dict[str, Any]) -> list[SchemaField]:
    """Normalize ``stats['schema']`` into :class:`SchemaField` objects."""
    raw = stats.get("schema")
    if raw is None:
        return []
    if isinstance(raw, list) and raw and isinstance(raw[0], SchemaField):
        return raw  # type: ignore[return-value]
    out: list[SchemaField] = []
    for row in raw or []:
        if isinstance(row, dict):
            out.append(
                SchemaField(
                    name=str(row["name"]),
                    type=str(row.get("type", "STRING")).upper(),
                    nullable=bool(row.get("nullable", True)),
                )
            )
        else:
            msg = "Each schema entry in stats must be a dict with at least 'name'"
            raise ValueError(msg)
    return out


def validate_schema(contract: DataContract, stats: dict[str, Any]) -> list[ContractViolation]:
    violations: list[ContractViolation] = []
    actual_fields = {f.name: f for f in stats_schema_fields(stats)}
    for expected in contract.schema:
        if expected.name not in actual_fields:
            violations.append(
                ContractViolation(
                    violation_type="SCHEMA_MISMATCH",
                    severity="CRITICAL",
                    expected=f"column {expected.name!r} present",
                    actual="missing",
                    field_name=expected.name,
                    message=f"Missing required column {expected.name!r}",
                )
            )
            continue
        got = actual_fields[expected.name]
        if expected.type.upper() != got.type.upper():
            violations.append(
                ContractViolation(
                    violation_type="SCHEMA_MISMATCH",
                    severity="CRITICAL",
                    expected=expected.type,
                    actual=got.type,
                    field_name=expected.name,
                    message=f"Type mismatch for column {expected.name!r}",
                )
            )
        if not expected.nullable and got.nullable:
            violations.append(
                ContractViolation(
                    violation_type="SCHEMA_MISMATCH",
                    severity="WARNING",
                    expected="non-nullable",
                    actual="nullable",
                    field_name=expected.name,
                    message=f"Nullability softer than contract for {expected.name!r}",
                )
            )
    return violations


def validate_completeness(contract: DataContract, stats: dict[str, Any]) -> list[ContractViolation]:
    violations: list[ContractViolation] = []
    if "row_count" not in stats:
        return violations
    row_count = int(stats["row_count"])
    v = contract.validate_row_count(row_count)
    if v:
        violations.append(v)
    return violations


def validate_freshness(contract: DataContract, stats: dict[str, Any]) -> list[ContractViolation]:
    violations: list[ContractViolation] = []
    if contract.freshness_max_age_minutes is None:
        return violations
    data_as_of = _parse_data_as_of(stats)
    if data_as_of is None:
        violations.append(
            ContractViolation(
                violation_type="FRESHNESS",
                severity="WARNING",
                expected="data_as_of timestamp in stats",
                actual="missing",
                field_name=None,
                message="Cannot evaluate freshness without stats['data_as_of']",
            )
        )
        return violations
    now = datetime.now(timezone.utc)
    age_minutes = (now - data_as_of).total_seconds() / 60.0
    if age_minutes > contract.freshness_max_age_minutes:
        violations.append(
            ContractViolation(
                violation_type="FRESHNESS",
                severity="CRITICAL",
                expected=f"<= {contract.freshness_max_age_minutes} minutes old",
                actual=f"{age_minutes:.1f} minutes",
                field_name=None,
                message="Dataset is older than freshness_max_age_minutes",
            )
        )
    return violations


def validate_sla(
    contract: DataContract,
    *,
    dag_run_start: datetime | None,
    dag_run_end: datetime | None,
) -> list[ContractViolation]:
    violations: list[ContractViolation] = []
    if contract.sla_completion_minutes is None or dag_run_start is None:
        return violations
    end = dag_run_end or datetime.now(timezone.utc)
    if dag_run_start.tzinfo is None:
        dag_run_start = dag_run_start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    duration_minutes = (end - dag_run_start).total_seconds() / 60.0
    if duration_minutes > contract.sla_completion_minutes:
        violations.append(
            ContractViolation(
                violation_type="SLA",
                severity="WARNING",
                expected=f"<= {contract.sla_completion_minutes} minutes",
                actual=f"{duration_minutes:.1f} minutes",
                field_name=None,
                message="DAG run exceeded sla_completion_minutes",
            )
        )
    return violations


def compute_schema_diff(contract: DataContract, stats: dict[str, Any]) -> SchemaDiff:
    expected = {f.name: f for f in contract.schema}
    actual = {f.name: f for f in stats_schema_fields(stats)}
    removed = sorted(set(expected) - set(actual))
    added = sorted(set(actual) - set(expected))
    type_changes: list[str] = []
    for name in sorted(set(expected) & set(actual)):
        if expected[name].type.upper() != actual[name].type.upper():
            type_changes.append(name)
    breaking: list[str] = []
    for name in removed:
        if not expected[name].nullable:
            breaking.append(f"removed required field {name!r}")
    for name in type_changes:
        breaking.append(f"type change for {name!r}")
    return SchemaDiff(
        breaking_changes=breaking,
        new_fields=added,
        removed_fields=removed,
        type_changes=type_changes,
    )
