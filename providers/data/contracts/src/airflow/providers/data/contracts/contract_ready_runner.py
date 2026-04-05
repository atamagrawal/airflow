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
"""Shared poke logic for :class:`ContractReadySensor` and task decorators."""
from __future__ import annotations

from datetime import datetime, timezone

from airflow.providers.common.compat.sdk import AirflowException
from airflow.providers.data.contracts.hooks.base_catalog import get_catalog_hook


def parse_contract_ready_time(value: str | None) -> datetime | None:
    """Parse ISO-8601 timestamps used by :class:`ContractReadySensor`."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def contract_ready_poke(
    *,
    catalog_conn_id: str,
    dataset_urn: str,
    min_update_time: str | None,
    fail_on_breach: bool,
) -> bool:
    """
    Return ``True`` when the contract is ``ACTIVE`` and meets the optional validation-time gate.

    Used by :class:`~airflow.providers.data.contracts.sensors.contract_ready.ContractReadySensor`
    and :class:`~airflow.providers.data.contracts_decorators.decorators.contract_ready._ContractReadyDecoratedSensor`.
    """
    hook = get_catalog_hook(catalog_conn_id=catalog_conn_id)
    contract = hook.get_contract(dataset_urn)
    status = contract.status.upper()

    if fail_on_breach and status == "BREACHED":
        msg = f"Contract for {dataset_urn} is BREACHED; failing sensor as requested"
        raise AirflowException(msg)

    if status != "ACTIVE":
        return False

    min_u = parse_contract_ready_time(min_update_time)
    if min_u and contract.last_validated_at:
        lv = contract.last_validated_at
        if lv.tzinfo is None:
            lv = lv.replace(tzinfo=timezone.utc)
        if lv < min_u:
            return False
    elif min_u and not contract.last_validated_at:
        return False

    return True
