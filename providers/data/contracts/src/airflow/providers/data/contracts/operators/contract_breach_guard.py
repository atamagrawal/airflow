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

from airflow.models import Variable
from airflow.providers.common.compat.sdk import AirflowException, AirflowSkipException, BaseOperator
from airflow.providers.data.contracts.hooks.base_catalog import get_catalog_hook

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context

OnBreach = Literal["fail", "skip", "warn"]


class ContractBreachGuardOperator(BaseOperator):
    """
    Consumer-side gate: ensure upstream datasets are not in ``BREACHED`` status.

    Set Airflow Variable ``override_var`` to ``true`` / ``1`` / ``yes`` for a break-glass bypass.
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
        if self.override_var:
            flag = Variable.get(self.override_var, default_var="0").lower()
            if flag in {"1", "true", "yes", "on"}:
                self.log.info("Contract guard bypassed via Variable %s", self.override_var)
                return

        hook = get_catalog_hook(catalog_conn_id=self.catalog_conn_id)
        breached: list[str] = []
        for urn in self.dataset_urns:
            status = hook.get_contract_status(urn)
            if status.upper() == "BREACHED":
                breached.append(urn)

        if not breached:
            return

        msg = "Upstream contract breach for: " + ", ".join(breached)
        if self.on_breach == "fail":
            raise AirflowException(msg)
        if self.on_breach == "skip":
            raise AirflowSkipException(msg)
        self.log.warning(msg)
