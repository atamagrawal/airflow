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

from airflow.providers.common.compat.sdk import AirflowException, BaseSensorOperator
from airflow.providers.data.contracts.hooks.base_catalog import get_catalog_hook

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


class ContractReadySensor(BaseSensorOperator):
    """
    Wait until a dataset contract is ``ACTIVE`` and optionally validated after a minimum time.

    Template ``min_update_time`` with ``data_interval_end`` or similar for daily producers.
    """

    template_fields: Sequence[str] = ("dataset_urn", "min_update_time")

    def __init__(
        self,
        *,
        catalog_conn_id: str,
        dataset_urn: str,
        min_update_time: str | None = None,
        fail_on_breach: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.catalog_conn_id = catalog_conn_id
        self.dataset_urn = dataset_urn
        self.min_update_time = min_update_time
        self.fail_on_breach = fail_on_breach

    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def poke(self, context: Context) -> bool:
        hook = get_catalog_hook(catalog_conn_id=self.catalog_conn_id)
        contract = hook.get_contract(self.dataset_urn)
        status = contract.status.upper()

        if self.fail_on_breach and status == "BREACHED":
            msg = f"Contract for {self.dataset_urn} is BREACHED; failing sensor as requested"
            raise AirflowException(msg)

        if status != "ACTIVE":
            return False

        min_u = self._parse_dt(self.min_update_time)
        if min_u and contract.last_validated_at:
            lv = contract.last_validated_at
            if lv.tzinfo is None:
                lv = lv.replace(tzinfo=timezone.utc)
            if lv < min_u:
                return False
        elif min_u and not contract.last_validated_at:
            # Without a stamp we cannot prove freshness — keep waiting.
            return False

        return True
