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
from airflow.providers.data.contracts.contract_trigger_user_runner import (
    allowed_trigger_users_from_contract_file,
    run_trigger_user_guard,
)

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context

WhenTriggeringUserMissing = Literal["allow", "fail", "skip"]
OnUnauthorized = Literal["fail", "skip", "warn", "pause_dag"]


class ContractTriggerUserGuardOperator(BaseOperator):
    """
    Gate a DAG run using ``DagRun.triggering_user_name`` (UI / REST / CLI manual triggers).

    Provide **either** ``allowed_users`` **or** ``contract_yaml_path``. The latter loads
    ``allowed_trigger_users`` from the contract file (same schema as catalog-lite YAML).

    Scheduled runs usually have no triggering user; control that with ``when_triggering_user_missing``.

    For TaskFlow DAGs, install ``apache-airflow-providers-data-contracts-decorators`` and use
    ``contract_trigger_user_guard_task`` or ``with_contract_trigger_user_from_yaml`` with ``@task``.
    """

    template_fields: Sequence[str] = ("allowed_users", "contract_yaml_path")

    def __init__(
        self,
        *,
        allowed_users: list[str] | None = None,
        contract_yaml_path: str | None = None,
        case_insensitive: bool = True,
        when_triggering_user_missing: WhenTriggeringUserMissing = "allow",
        on_unauthorized: OnUnauthorized = "fail",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        has_users = allowed_users is not None and len(allowed_users) > 0
        has_yaml = contract_yaml_path is not None and str(contract_yaml_path).strip() != ""
        if has_users == has_yaml:
            msg = "Set exactly one of allowed_users (non-empty list) or contract_yaml_path"
            raise ValueError(msg)
        self.allowed_users = allowed_users
        self.contract_yaml_path = contract_yaml_path
        self.case_insensitive = case_insensitive
        self.when_triggering_user_missing = when_triggering_user_missing
        self.on_unauthorized = on_unauthorized

    def execute(self, context: Context) -> None:
        if self.contract_yaml_path:
            allowed_list = allowed_trigger_users_from_contract_file(str(self.contract_yaml_path))
        else:
            allowed_list = list(self.allowed_users or [])
        dr = context.get("dag_run")
        triggering_user_name = getattr(dr, "triggering_user_name", None) if dr else None
        run_trigger_user_guard(
            dag_id=context["dag"].dag_id,
            triggering_user_name=triggering_user_name,
            allowed_users=allowed_list,
            case_insensitive=self.case_insensitive,
            when_triggering_user_missing=self.when_triggering_user_missing,
            on_unauthorized=self.on_unauthorized,
            log=self.log,
        )
