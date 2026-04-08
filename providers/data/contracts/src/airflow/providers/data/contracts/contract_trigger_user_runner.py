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
"""Check DagRun.triggering_user_name against an allow-list (manual / API / UI triggers)."""

from __future__ import annotations

import logging
from typing import Literal

from airflow.providers.common.compat.sdk import AirflowException, AirflowSkipException


def allowed_trigger_users_from_contract_file(contract_file_path: str) -> list[str]:
    """
    Load ``allowed_trigger_users`` from a contract YAML/JSON file on disk.

    The file must define the ``allowed_trigger_users`` key (use an empty list to deny
    every non-scheduled triggering user). If the key is absent, a :class:`ValueError`
    is raised so misconfiguration is visible when using ``contract_yaml_path`` on the guard.
    """
    from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook

    contract = YamlDataContractHook.load_contract_from_file(contract_file_path)
    raw = contract.allowed_trigger_users
    if raw is None:
        msg = (
            f"Contract file {contract_file_path!r} does not define 'allowed_trigger_users'. "
            "Add a YAML list of Airflow user names (DagRun.triggering_user_name), or use "
            "allowed_users=... on the operator instead of contract_yaml_path."
        )
        raise ValueError(msg)
    return list(raw)


WhenTriggeringUserMissing = Literal["allow", "fail", "skip"]
OnUnauthorized = Literal["fail", "skip", "warn", "pause_dag"]


def _normalize_user(name: str | None, *, case_insensitive: bool) -> str | None:
    if name is None:
        return None
    stripped = str(name).strip()
    if not stripped:
        return None
    return stripped.casefold() if case_insensitive else stripped


def triggering_user_is_allowed(
    *,
    triggering_user_name: str | None,
    allowed_users: list[str],
    case_insensitive: bool,
) -> bool:
    """Return whether ``triggering_user_name`` matches one of ``allowed_users``."""
    allowed_norm = {
        n for u in allowed_users if (n := _normalize_user(u, case_insensitive=case_insensitive)) is not None
    }
    if not allowed_norm:
        return False
    tu = _normalize_user(triggering_user_name, case_insensitive=case_insensitive)
    if tu is None:
        return False
    return tu in allowed_norm


def run_trigger_user_guard(
    *,
    dag_id: str,
    triggering_user_name: str | None,
    allowed_users: list[str],
    case_insensitive: bool,
    when_triggering_user_missing: WhenTriggeringUserMissing,
    on_unauthorized: OnUnauthorized,
    log: logging.Logger,
) -> None:
    """
    Enforce DAG run triggering user policy.

    ``triggering_user_name`` is set for many manual, UI, and REST triggers; scheduled runs
    often leave it empty. Use ``when_triggering_user_missing`` for that case.

    Used by
    :class:`~airflow.providers.data.contracts.operators.contract_trigger_user_guard.ContractTriggerUserGuardOperator`,
    ``contract_trigger_user_guard_task``, and ``with_contract_trigger_user_from_yaml``.
    Allow-lists can be loaded from YAML with :func:`allowed_trigger_users_from_contract_file`.
    """
    if _normalize_user(triggering_user_name, case_insensitive=False) is None:
        if when_triggering_user_missing == "allow":
            return
        msg = (
            "DAG run has no triggering_user_name (typical for scheduled runs); "
            f"configured policy is {when_triggering_user_missing!r}"
        )
        if when_triggering_user_missing == "skip":
            raise AirflowSkipException(msg)
        raise AirflowException(msg)

    if triggering_user_is_allowed(
        triggering_user_name=triggering_user_name,
        allowed_users=allowed_users,
        case_insensitive=case_insensitive,
    ):
        return

    msg = (
        f"Triggering user {triggering_user_name!r} is not authorized for this DAG run "
        f"(allowed: {allowed_users!r})"
    )
    if on_unauthorized == "warn":
        log.warning("%s", msg)
        return
    if on_unauthorized == "skip":
        raise AirflowSkipException(msg)
    if on_unauthorized == "pause_dag":
        from sqlalchemy import select

        from airflow.models.dag import DagModel
        from airflow.utils.session import create_session

        log.warning("Pausing DAG %s: %s", dag_id, msg)
        with create_session() as session:
            dm = session.scalar(select(DagModel).where(DagModel.dag_id == dag_id).limit(1))
            if dm is None:
                log.error("DagModel not found for dag_id=%s; cannot pause", dag_id)
            else:
                dm.is_paused = True
        raise AirflowException(msg + f"; DAG {dag_id!r} has been paused")

    raise AirflowException(msg)
