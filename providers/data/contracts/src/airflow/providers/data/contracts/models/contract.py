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

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SchemaField:
    """Single column or field in a tabular contract schema."""

    name: str
    type: str
    nullable: bool = True
    description: str | None = None
    primary_key: bool = False
    partition_key: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class ContractViolation:
    """One failed clause when validating pipeline output against a contract."""

    violation_type: str
    severity: str
    expected: str
    actual: str
    field_name: str | None
    message: str


@dataclass
class DataContract:
    """In-memory representation of a data contract (catalog or YAML)."""

    contract_id: str
    dataset_urn: str
    dataset_name: str
    version: int
    status: str
    schema: list[SchemaField]
    schema_compatibility: str = "BACKWARD"
    schema_version: int = 1
    freshness_cron: str | None = None
    freshness_max_age_minutes: int | None = None
    min_row_count: int | None = None
    max_row_count: int | None = None
    required_partitions: list[str] | None = None
    sla_completion_minutes: int | None = None
    producer_team: str = ""
    producer_dag_id: str | None = None
    consumer_teams: list[str] = field(default_factory=list)
    consumer_dag_ids: list[str] = field(default_factory=list)
    data_steward: str | None = None
    catalog_url: str = ""
    last_validated_at: datetime | None = None
    last_breach_at: datetime | None = None
    tags: list[str] = field(default_factory=list)

    def get_schema_field(self, name: str) -> SchemaField | None:
        for f in self.schema:
            if f.name == name:
                return f
        return None

    def validate_row_count(self, actual: int) -> ContractViolation | None:
        if self.min_row_count is not None and actual < self.min_row_count:
            return ContractViolation(
                violation_type="COMPLETENESS",
                severity="CRITICAL",
                expected=f">= {self.min_row_count}",
                actual=str(actual),
                field_name=None,
                message="Row count below contract minimum",
            )
        if self.max_row_count is not None and actual > self.max_row_count:
            return ContractViolation(
                violation_type="COMPLETENESS",
                severity="WARNING",
                expected=f"<= {self.max_row_count}",
                actual=str(actual),
                field_name=None,
                message="Row count above contract maximum",
            )
        return None

    def is_breaking_change(self, new_schema: list[SchemaField]) -> bool:
        """Return True if any required (non-nullable) field is removed."""
        old_names = {f.name for f in self.schema if not f.nullable}
        new_names = {f.name for f in new_schema}
        return bool(old_names - new_names)


def schema_field_from_mapping(row: dict[str, Any]) -> SchemaField:
    return SchemaField(
        name=str(row["name"]),
        type=str(row.get("type", "STRING")).upper(),
        nullable=bool(row.get("nullable", True)),
        description=row.get("description"),
        primary_key=bool(row.get("primary_key", False)),
        partition_key=bool(row.get("partition_key", False)),
        tags=list(row.get("tags") or []),
    )


def data_contract_from_mapping(data: dict[str, Any], *, catalog_url: str = "") -> DataContract:
    """Build :class:`DataContract` from a YAML/JSON mapping (catalog-lite or embedded contract)."""
    schema_raw = data.get("schema") or []
    schema: list[SchemaField] = []
    for entry in schema_raw:
        if not isinstance(entry, dict):
            msg = "schema entries must be mappings"
            raise ValueError(msg)
        schema.append(schema_field_from_mapping(entry))

    return DataContract(
        contract_id=str(data.get("contract_id") or data.get("dataset_urn", "unknown")),
        dataset_urn=str(data["dataset_urn"]),
        dataset_name=str(data.get("dataset_name") or data["dataset_urn"]),
        version=int(data.get("version", 1)),
        status=str(data.get("status", "ACTIVE")).upper(),
        schema=schema,
        schema_compatibility=str(data.get("schema_compatibility", "BACKWARD")).upper(),
        schema_version=int(data.get("schema_version", 1)),
        freshness_cron=data.get("freshness_cron"),
        freshness_max_age_minutes=data.get("freshness_max_age_minutes"),
        min_row_count=data.get("min_row_count"),
        max_row_count=data.get("max_row_count"),
        required_partitions=list(data["required_partitions"]) if data.get("required_partitions") else None,
        sla_completion_minutes=data.get("sla_completion_minutes"),
        producer_team=str(data.get("producer_team", "")),
        producer_dag_id=data.get("producer_dag_id"),
        consumer_teams=list(data.get("consumer_teams") or []),
        consumer_dag_ids=list(data.get("consumer_dag_ids") or []),
        data_steward=data.get("data_steward"),
        catalog_url=str(data.get("catalog_url") or catalog_url),
        last_validated_at=parse_optional_iso_datetime(data.get("last_validated_at")),
        last_breach_at=parse_optional_iso_datetime(data.get("last_breach_at")),
        tags=list(data.get("tags") or []),
    )


def parse_optional_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)
