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
"""Shared breach-guard logic for :class:`ContractBreachGuardOperator` and task decorators."""
from __future__ import annotations

import logging
from typing import Literal

from airflow.models import Variable
from airflow.providers.common.compat.sdk import AirflowException, AirflowSkipException
from airflow.providers.data.contracts.hooks.base_catalog import get_catalog_hook

OnBreach = Literal["fail", "skip", "warn"]


def run_contract_breach_guard(
    *,
    catalog_conn_id: str,
    dataset_urns: list[str],
    on_breach: OnBreach,
    override_var: str | None,
    log: logging.Logger,
) -> None:
    """
    Fail, skip, or warn when any upstream contract is ``BREACHED``.

    Used by :class:`~airflow.providers.data.contracts.operators.contract_breach_guard.ContractBreachGuardOperator`
    and :func:`~airflow.providers.data.contracts_decorators.decorators.contract_breach_guard.contract_breach_guard_task`.
    """
    if override_var:
        flag = Variable.get(override_var, default_var="0").lower()
        if flag in {"1", "true", "yes", "on"}:
            log.info("Contract guard bypassed via Variable %s", override_var)
            return

    hook = get_catalog_hook(catalog_conn_id=catalog_conn_id)
    breached: list[str] = []
    for urn in dataset_urns:
        status = hook.get_contract_status(urn)
        if status.upper() == "BREACHED":
            breached.append(urn)

    if not breached:
        return

    msg = "Upstream contract breach for: " + ", ".join(breached)
    if on_breach == "fail":
        raise AirflowException(msg)
    if on_breach == "skip":
        raise AirflowSkipException(msg)
    log.warning("%s", msg)
