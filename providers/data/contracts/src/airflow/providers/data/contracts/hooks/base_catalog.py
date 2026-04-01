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

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

from airflow.providers.common.compat.sdk import BaseHook
from airflow.providers.data.contracts.exceptions import UnsupportedCatalogConnectionError
from airflow.providers.data.contracts.models.schema_diff import SchemaDiff

if TYPE_CHECKING:
    from airflow.providers.data.contracts.models.contract import ContractViolation, DataContract


class BaseCatalogHook(BaseHook, ABC):
    """Catalog integration used by data-contract operators."""

    @abstractmethod
    def get_contract(self, dataset_urn: str) -> DataContract:
        """Return the active contract for ``dataset_urn``."""

    @abstractmethod
    def get_contract_status(self, dataset_urn: str) -> str:
        """Return a coarse status string such as ``ACTIVE`` or ``BREACHED``."""

    def report_breach(
        self,
        dataset_urn: str,
        violations: list[ContractViolation],
        *,
        dag_id: str,
        run_id: str,
    ) -> str | None:
        """Optionally persist a breach; default implementation is a no-op."""
        return None

    def update_contract_status(
        self,
        dataset_urn: str,
        status: str,
        *,
        last_validated_at: datetime | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        """Optionally update catalog status after a successful publish."""

    def emit_lineage(
        self,
        output_urn: str,
        input_urns: list[str],
        *,
        transformation_description: str | None = None,
    ) -> None:
        """Optionally emit lineage edges for the output dataset."""

    def check_schema_compatibility(
        self,
        dataset_urn: str,
        proposed_schema: list[Any],
        *,
        compatibility_mode: str = "BACKWARD",
    ) -> SchemaDiff:
        """Compare ``proposed_schema`` to the catalog contract (default: local diff only)."""
        contract = self.get_contract(dataset_urn)
        from airflow.providers.data.contracts.models.contract import SchemaField

        proposed: list[SchemaField] = []
        for item in proposed_schema:
            if isinstance(item, SchemaField):
                proposed.append(item)
            elif isinstance(item, dict):
                proposed.append(
                    SchemaField(
                        name=str(item["name"]),
                        type=str(item.get("type", "STRING")).upper(),
                        nullable=bool(item.get("nullable", True)),
                    )
                )
            else:
                msg = "proposed_schema entries must be SchemaField or dict"
                raise ValueError(msg)
        expected = {f.name: f for f in contract.schema}
        actual = {f.name: f for f in proposed}
        removed = sorted(set(expected) - set(actual))
        added = sorted(set(actual) - set(expected))
        type_changes = [
            n
            for n in sorted(set(expected) & set(actual))
            if expected[n].type.upper() != actual[n].type.upper()
        ]
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


def get_catalog_hook(*, catalog_conn_id: str) -> BaseCatalogHook:
    """Instantiate the hook declared by the Airflow connection type."""
    conn = BaseHook.get_connection(catalog_conn_id)
    if conn.conn_type == "datahub":
        from airflow.providers.data.contracts.hooks.datahub import DataHubCatalogHook

        return DataHubCatalogHook(catalog_conn_id=catalog_conn_id)
    if conn.conn_type == "data_contract_yaml":
        from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook

        return YamlDataContractHook(catalog_conn_id=catalog_conn_id)
    msg = (
        f"Connection {catalog_conn_id!r} has unsupported type {conn.conn_type!r}; "
        "use 'datahub' or 'data_contract_yaml'."
    )
    raise UnsupportedCatalogConnectionError(msg)
