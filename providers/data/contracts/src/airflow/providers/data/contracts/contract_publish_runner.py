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
"""Shared publish logic for :class:`ContractPublishOperator` and task decorators."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from airflow.providers.data.contracts.hooks.base_catalog import get_catalog_hook

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


def publish_contract_run(
    *,
    context: Context,
    catalog_conn_id: str,
    dataset_urn: str,
    upstream_urns: list[str],
    stats: dict | None,
    update_contract_status: bool,
    contract_status: str,
    emit_run_facet: bool,
) -> None:
    """
    Emit lineage and optionally update contract status in the catalog.

    Used by :class:`~airflow.providers.data.contracts.operators.contract_publish.ContractPublishOperator`
    and :func:`~airflow.providers.data.contracts_decorators.decorators.contract_publish.contract_publish_task`.
    """
    hook = get_catalog_hook(catalog_conn_id=catalog_conn_id)

    desc = None
    if emit_run_facet:
        desc = f"Airflow dag_id={context['dag'].dag_id} run_id={context['run_id']}"

    hook.emit_lineage(
        dataset_urn,
        upstream_urns,
        transformation_description=desc,
    )

    if update_contract_status:
        hook.update_contract_status(
            dataset_urn,
            contract_status,
            last_validated_at=datetime.now(timezone.utc),
            stats=stats,
        )
