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

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from airflow.providers.data.contracts.exceptions import CatalogApiError, ContractResolutionError
from airflow.providers.data.contracts.hooks.base_catalog import BaseCatalogHook
from airflow.providers.data.contracts.models.contract import (
    ContractViolation,
    DataContract,
    SchemaField,
    data_contract_from_mapping,
    parse_optional_iso_datetime,
)

log = logging.getLogger(__name__)


def _schema_fields_from_schema_metadata(sm: dict[str, Any]) -> list[SchemaField]:
    fields: list[SchemaField] = []
    for entry in sm.get("fields") or []:
        path = entry.get("fieldPath") or entry.get("path") or entry.get("name")
        if not path:
            continue
        type_str = "STRING"
        t = entry.get("type")
        if isinstance(t, dict):
            for key in t:
                if key.endswith("Type"):
                    type_str = key.rsplit(".", 1)[-1].replace("Type", "").upper()
                    break
        nullable = bool(entry.get("nullable", True))
        fields.append(SchemaField(name=str(path), type=type_str, nullable=nullable))
    return fields


def _dig_aspects(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize different DataHub OpenAPI shapes to aspect_name -> value dict."""
    if "aspects" in payload and isinstance(payload["aspects"], dict):
        return payload["aspects"]
    if "value" in payload and isinstance(payload["value"], dict):
        return payload["value"]
    return payload


class DataHubCatalogHook(BaseCatalogHook):
    """
    Read contracts from DataHub GMS OpenAPI.

    Connection fields:

    * **Host** — GMS base URL (unless ``gms_url`` is set in extras)
    * **Password** — personal access token (sent as ``Authorization: Bearer``)

    Extras:

    * ``gms_url`` — overrides host
    * ``timeout_sec`` — HTTP timeout (default 30)
    """

    conn_name_attr = "catalog_conn_id"
    default_conn_name = "datahub_default"
    conn_type = "datahub"
    hook_name = "DataHub (data contracts)"

    def __init__(self, catalog_conn_id: str = default_conn_name) -> None:
        super().__init__()
        self.catalog_conn_id = catalog_conn_id
        conn = self.get_connection(catalog_conn_id)
        extra = conn.extra_dejson
        self.gms_url = (extra.get("gms_url") or conn.host or "").rstrip("/")
        if not self.gms_url:
            msg = "DataHub connection requires host or extras.gms_url"
            raise ValueError(msg)
        self._token = conn.password or extra.get("token")
        self._timeout = int(extra.get("timeout_sec", 30))

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _fetch_entity_v3(self, dataset_urn: str) -> dict[str, Any]:
        encoded = quote(dataset_urn, safe="")
        url = f"{self.gms_url}/openapi/v3/entity/dataPlatform/dataset/{encoded}"
        resp = requests.get(url, headers=self._headers(), timeout=self._timeout)
        if resp.status_code == 404:
            msg = f"Dataset entity not found for URN {dataset_urn!r}"
            raise ContractResolutionError(msg)
        if not resp.ok:
            msg = f"DataHub GMS returned HTTP {resp.status_code}: {resp.text[:500]}"
            raise CatalogApiError(msg)
        try:
            return resp.json()
        except json.JSONDecodeError as e:
            msg = "DataHub GMS returned non-JSON body"
            raise CatalogApiError(msg) from e

    def get_contract(self, dataset_urn: str) -> DataContract:
        body = self._fetch_entity_v3(dataset_urn)
        aspects = _dig_aspects(body)
        catalog_url = f"{self.gms_url}/dataset/{quote(dataset_urn, safe='')}"

        props = aspects.get("datasetProperties") or aspects.get("com.linkedin.dataset.DatasetProperties")
        if isinstance(props, dict) and "value" in props:
            props = props["value"]
        custom: dict[str, str] = {}
        if isinstance(props, dict):
            custom = props.get("customProperties") or {}

        embedded = custom.get("airflow_data_contract")
        if embedded:
            try:
                mapping = json.loads(embedded)
            except json.JSONDecodeError as e:
                msg = "Invalid JSON in customProperties.airflow_data_contract"
                raise ContractResolutionError(msg) from e
            if not isinstance(mapping, dict):
                msg = "airflow_data_contract must be a JSON object"
                raise ContractResolutionError(msg)
            mapping.setdefault("dataset_urn", dataset_urn)
            return data_contract_from_mapping(mapping, catalog_url=catalog_url)

        sm = aspects.get("schemaMetadata") or aspects.get("com.linkedin.schema.SchemaMetadata")
        if isinstance(sm, dict) and "value" in sm:
            sm = sm["value"]
        schema_fields = _schema_fields_from_schema_metadata(sm) if isinstance(sm, dict) else []

        status = (custom.get("contract_status") or custom.get("airflow_contract_status") or "ACTIVE").upper()
        version = int(custom.get("contract_version") or custom.get("airflow_contract_version") or 1)

        return DataContract(
            contract_id=dataset_urn,
            dataset_urn=dataset_urn,
            dataset_name=custom.get("dataset_name") or dataset_urn,
            version=version,
            status=status,
            schema=schema_fields,
            schema_compatibility=(custom.get("schema_compatibility") or "BACKWARD").upper(),
            schema_version=int(custom.get("schema_version") or 1),
            freshness_max_age_minutes=_optional_int(custom.get("freshness_max_age_minutes")),
            min_row_count=_optional_int(custom.get("min_row_count")),
            max_row_count=_optional_int(custom.get("max_row_count")),
            sla_completion_minutes=_optional_int(custom.get("sla_completion_minutes")),
            producer_team=str(custom.get("producer_team", "")),
            catalog_url=catalog_url,
            last_validated_at=parse_optional_iso_datetime(custom.get("last_validated_at")),
            last_breach_at=parse_optional_iso_datetime(custom.get("last_breach_at")),
        )

    def get_contract_status(self, dataset_urn: str) -> str:
        return self.get_contract(dataset_urn).status

    def report_breach(
        self,
        dataset_urn: str,
        violations: list[ContractViolation],
        *,
        dag_id: str,
        run_id: str,
    ) -> str | None:
        log.warning(
            "Data contract breach for %s (dag=%s run=%s): %s",
            dataset_urn,
            dag_id,
            run_id,
            [v.message for v in violations],
        )
        # Full GMS aspect writes are environment-specific; log + return synthetic id for traceability.
        return f"logged:{dataset_urn}"

    def update_contract_status(
        self,
        dataset_urn: str,
        status: str,
        *,
        last_validated_at: datetime | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        when = last_validated_at or datetime.now(timezone.utc)
        log.info(
            "Would update contract status for %s -> %s at %s (stats=%s)",
            dataset_urn,
            status,
            when.isoformat(),
            stats,
        )

    def emit_lineage(
        self,
        output_urn: str,
        input_urns: list[str],
        *,
        transformation_description: str | None = None,
    ) -> None:
        log.info(
            "Lineage emit requested: output=%s inputs=%s (%s)",
            output_urn,
            input_urns,
            transformation_description,
        )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)
