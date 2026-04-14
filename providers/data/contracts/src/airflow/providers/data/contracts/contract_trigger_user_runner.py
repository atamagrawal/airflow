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
from collections.abc import Mapping
from typing import Any, Literal

from airflow.providers.common.compat.sdk import AirflowException, AirflowSkipException

WhenTriggeringUserMissing = Literal["allow", "fail", "skip"]
OnUnauthorized = Literal["fail", "skip", "warn", "pause_dag"]


def validate_trigger_user_guard_params(
    *,
    allowed_users: list[str] | None,
    contract_yaml_path: str | None,
    catalog_conn_id: str | None,
    dataset_urn: str | None,
) -> None:
    """
    Validate operator / task configuration for the trigger-user guard.

    * **Inline:** non-empty ``allowed_users`` — do not set ``dataset_urn``,
      ``contract_yaml_path``, or ``catalog_conn_id``.
    * **From the catalog hook:** non-empty ``dataset_urn`` only. The connection/path are
      system-managed and must not be set in DAG code. Resolution always uses the platform
      default YAML catalog connection
      (:attr:`~airflow.providers.data.contracts.hooks.local_yaml.YamlDataContractHook.default_conn_name`),
      which you (or your vendor) pre-provision with ``extras.contracts`` URN → file paths.
    """
    has_inline = allowed_users is not None and len(allowed_users) > 0
    has_yaml = contract_yaml_path is not None and str(contract_yaml_path).strip() != ""
    has_conn = catalog_conn_id is not None and str(catalog_conn_id).strip() != ""
    has_urn = dataset_urn is not None and str(dataset_urn).strip() != ""

    if has_inline:
        if has_yaml or has_conn or has_urn:
            msg = "allowed_users cannot be combined with contract_yaml_path, catalog_conn_id, or dataset_urn"
            raise ValueError(msg)
        return

    if has_yaml or has_conn:
        msg = "contract_yaml_path and catalog_conn_id are system-managed; pass dataset_urn only"
        raise ValueError(msg)

    if not has_urn:
        msg = "dataset_urn is required when resolving allow-list from a catalog contract"
        raise ValueError(msg)


def resolve_allowed_trigger_users_for_execution(
    *,
    allowed_users: list[str] | None,
    contract_yaml_path: str | None,
    catalog_conn_id: str | None,
    dataset_urn: str | None,
) -> list[str]:
    """
    Resolve the allow-list after templating.

    Delegates to :func:`~airflow.providers.data.contracts.contract_validate_runner.load_contract_for_validation`
    so the guard uses the platform catalog path with system-managed defaults.

    Call only when :func:`validate_trigger_user_guard_params` has already passed.
    """
    if allowed_users is not None and len(allowed_users) > 0:
        return list(allowed_users)

    from airflow.providers.data.contracts.contract_validate_runner import load_contract_for_validation
    from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook

    contract = load_contract_for_validation(
        catalog_conn_id=YamlDataContractHook.default_conn_name,
        dataset_urn=str(dataset_urn),
        contract_yaml_path=None,
    )
    raw = contract.allowed_trigger_users
    if raw is None:
        msg = (
            f"Contract {dataset_urn!r} does not define 'allowed_trigger_users'. "
            "Add the key to the contract or use allowed_users=... on the operator."
        )
        raise ValueError(msg)
    return list(raw)


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

    Used by :class:`~airflow.providers.data.contracts.operators.contract_trigger_user_guard.ContractTriggerUserGuardOperator`,
    :func:`run_trigger_user_guard_for_context`, and ``contract_trigger_user_guard_task``.
    Contract allow-lists are resolved with :func:`resolve_allowed_trigger_users_for_execution`
    (via the catalog hook for a ``dataset_urn``).
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


def run_trigger_user_guard_for_context(
    context: Mapping[str, Any],
    *,
    allowed_users: list[str],
    case_insensitive: bool,
    when_triggering_user_missing: WhenTriggeringUserMissing,
    on_unauthorized: OnUnauthorized,
    log: logging.Logger,
) -> None:
    """Run :func:`run_trigger_user_guard` using ``dag`` / ``dag_run`` from an Airflow task context."""
    dag = context["dag"]
    dr = context.get("dag_run")
    triggering_user_name = getattr(dr, "triggering_user_name", None) if dr else None
    run_trigger_user_guard(
        dag_id=dag.dag_id,
        triggering_user_name=triggering_user_name,
        allowed_users=allowed_users,
        case_insensitive=case_insensitive,
        when_triggering_user_missing=when_triggering_user_missing,
        on_unauthorized=on_unauthorized,
        log=log,
    )
