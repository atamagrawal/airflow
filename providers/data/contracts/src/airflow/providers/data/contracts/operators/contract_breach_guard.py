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
from typing import TYPE_CHECKING, Literal

from airflow.providers.common.compat.sdk import BaseOperator
from airflow.providers.data.contracts.contract_breach_runner import run_contract_breach_guard

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context

OnBreach = Literal["fail", "skip", "warn"]


class ContractBreachGuardOperator(BaseOperator):
    """
    Consumer-side gate: ensure upstream datasets are not in ``BREACHED`` status.

    Set Airflow Variable ``override_var`` to ``true`` / ``1`` / ``yes`` for a break-glass bypass.

    For TaskFlow-style DAGs, install ``apache-airflow-providers-data-contracts-decorators`` and use
    :func:`~airflow.providers.data.contracts_decorators.decorators.contract_breach_guard.contract_breach_guard_task`.
    """

    template_fields: Sequence[str] = ("dataset_urns",)

    def __init__(
        self,
        *,
        catalog_conn_id: str,
        dataset_urns: list[str],
        on_breach: OnBreach = "fail",
        override_var: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.catalog_conn_id = catalog_conn_id
        self.dataset_urns = dataset_urns
        self.on_breach = on_breach
        self.override_var = override_var

    def execute(self, context: Context) -> None:
        run_contract_breach_guard(
            catalog_conn_id=self.catalog_conn_id,
            dataset_urns=self.dataset_urns,
            on_breach=self.on_breach,
            override_var=self.override_var,
            log=self.log,
        )
