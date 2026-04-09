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

from airflow.providers.common.compat.sdk import BaseOperator
from airflow.providers.data.contracts.contract_trigger_user_runner import (
    OnUnauthorized,
    WhenTriggeringUserMissing,
    resolve_allowed_trigger_users_for_execution,
    run_trigger_user_guard_for_context,
    validate_trigger_user_guard_params,
)

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


class ContractTriggerUserGuardOperator(BaseOperator):
    """
    Restrict who may trigger a DAG run using ``DagRun.triggering_user_name``.

    **Either** pass **``allowed_users``** inline **or** give **``dataset_urn``** so
    ``allowed_trigger_users`` is read from the contract returned by the catalog hook (same stack as
    :class:`~airflow.providers.data.contracts.operators.contract_validate.ContractValidateOperator`).

    DAG authors normally set **only** ``dataset_urn``; the platform (or product) pre-provisions the
    default ``data_contract_yaml`` connection
    (:attr:`~airflow.providers.data.contracts.hooks.local_yaml.YamlDataContractHook.default_conn_name`)
    with ``extras.contracts`` mapping URNs to contract files.

    Scheduled runs often have no triggering user; control that with ``when_triggering_user_missing``.

    For TaskFlow DAGs, install ``apache-airflow-providers-data-contracts-decorators`` and use
    ``@task.contract_trigger_user_guard`` (``contract_trigger_user_guard_task``), or stack
    ``contract_trigger_user_guard`` under plain ``@task``.
    """

    template_fields: Sequence[str] = (
        "allowed_users",
        "dataset_urn",
    )

    def __init__(
        self,
        *,
        allowed_users: list[str] | None = None,
        dataset_urn: str | None = None,
        case_insensitive: bool = True,
        when_triggering_user_missing: WhenTriggeringUserMissing = "allow",
        on_unauthorized: OnUnauthorized = "fail",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        validate_trigger_user_guard_params(
            allowed_users=allowed_users,
            contract_yaml_path=None,
            catalog_conn_id=None,
            dataset_urn=dataset_urn,
        )
        self.allowed_users = allowed_users
        self.dataset_urn = dataset_urn
        self.case_insensitive = case_insensitive
        self.when_triggering_user_missing = when_triggering_user_missing
        self.on_unauthorized = on_unauthorized

    def execute(self, context: Context) -> None:
        allowed_list = resolve_allowed_trigger_users_for_execution(
            allowed_users=self.allowed_users,
            contract_yaml_path=None,
            catalog_conn_id=None,
            dataset_urn=self.dataset_urn,
        )
        run_trigger_user_guard_for_context(
            context,
            allowed_users=allowed_list,
            case_insensitive=self.case_insensitive,
            when_triggering_user_missing=self.when_triggering_user_missing,
            on_unauthorized=self.on_unauthorized,
            log=self.log,
        )
