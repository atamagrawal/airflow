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
from typing import TYPE_CHECKING

from airflow.providers.common.compat.sdk import BaseOperator
from airflow.providers.data.contracts.hooks.base_catalog import get_catalog_hook

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


class ContractPublishOperator(BaseOperator):
    """
    Producer-side task: emit lineage and stamp contract status in the catalog.

    Catalog-lite YAML connections only log; use a DataHub connection for real writes
    (ingest / OpenAPI patch is environment-specific and may require extra tooling).
    """

    template_fields: Sequence[str] = (
        "dataset_urn",
        "stats_xcom_task_id",
        "stats_xcom_key",
    )

    def __init__(
        self,
        *,
        catalog_conn_id: str,
        dataset_urn: str,
        upstream_urns: list[str] | None = None,
        stats_xcom_task_id: str | None = None,
        stats_xcom_key: str = "return_value",
        update_contract_status: bool = True,
        contract_status: str = "ACTIVE",
        emit_run_facet: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.catalog_conn_id = catalog_conn_id
        self.dataset_urn = dataset_urn
        self.upstream_urns = upstream_urns or []
        self.stats_xcom_task_id = stats_xcom_task_id
        self.stats_xcom_key = stats_xcom_key
        self.update_contract_status = update_contract_status
        self.contract_status = contract_status
        self.emit_run_facet = emit_run_facet

    def execute(self, context: Context) -> None:
        hook = get_catalog_hook(catalog_conn_id=self.catalog_conn_id)
        stats = None
        if self.stats_xcom_task_id:
            stats = context["ti"].xcom_pull(task_ids=self.stats_xcom_task_id, key=self.stats_xcom_key)
            if stats is not None and not isinstance(stats, dict):
                msg = "Stats from XCom must be a dict when stats_xcom_task_id is set"
                raise TypeError(msg)

        desc = None
        if self.emit_run_facet:
            desc = f"Airflow dag_id={context['dag'].dag_id} run_id={context['run_id']}"

        hook.emit_lineage(
            self.dataset_urn,
            self.upstream_urns,
            transformation_description=desc,
        )

        if self.update_contract_status:
            hook.update_contract_status(
                self.dataset_urn,
                self.contract_status,
                last_validated_at=datetime.now(timezone.utc),
                stats=stats,
            )
