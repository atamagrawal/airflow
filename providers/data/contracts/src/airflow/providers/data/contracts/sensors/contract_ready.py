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
from typing import TYPE_CHECKING

from airflow.providers.common.compat.sdk import BaseSensorOperator
from airflow.providers.data.contracts.contract_ready_runner import contract_ready_poke

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


class ContractReadySensor(BaseSensorOperator):
    """
    Wait until a dataset contract is ``ACTIVE`` and optionally validated after a minimum time.

    Template ``min_update_time`` with ``data_interval_end`` or similar for daily producers.

    For TaskFlow-style DAGs, install ``apache-airflow-providers-data-contracts-decorators`` and use
    :func:`~airflow.providers.data.contracts_decorators.decorators.contract_ready.contract_ready_task`.
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

    def poke(self, context: Context) -> bool:
        return contract_ready_poke(
            catalog_conn_id=self.catalog_conn_id,
            dataset_urn=self.dataset_urn,
            min_update_time=self.min_update_time,
            fail_on_breach=self.fail_on_breach,
        )
