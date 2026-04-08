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
"""Stack a contract YAML trigger-user check on any ``@task``."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any, Literal, TypeVar

from airflow.providers.data.contracts.contract_trigger_user_runner import (
    allowed_trigger_users_from_contract_file,
    run_trigger_user_guard,
)

WhenTriggeringUserMissing = Literal["allow", "fail", "skip"]
OnUnauthorized = Literal["fail", "skip", "warn", "pause_dag"]

F = TypeVar("F", bound=Callable[..., Any])


def with_contract_trigger_user_from_yaml(
    contract_yaml_path: str,
    *,
    case_insensitive: bool = True,
    when_triggering_user_missing: WhenTriggeringUserMissing = "allow",
    on_unauthorized: OnUnauthorized = "fail",
) -> Callable[[F], F]:
    """
    Decorate a callable that will run **under** ``@task`` so the guard runs first inside the task.

    The YAML file must define ``allowed_trigger_users`` (list of ``DagRun.triggering_user_name``
    values). ``contract_yaml_path`` supports Jinja templating using the task context (same as
    operator ``template_fields``).

    Apply decorators **bottom-up** (this decorator directly wraps your function, ``@task`` is outer)::

        @task
        @with_contract_trigger_user_from_yaml(CONTRACT_YAML)
        def my_task():
            return 1
    """

    def decorator(f: F) -> F:
        @wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Imported inside the wrapper so tests can patch ``airflow.sdk.get_current_context``.
            from airflow.sdk import get_current_context

            ctx = get_current_context()
            task = ctx["task"]
            path = str(
                task.render_template(
                    contract_yaml_path,
                    ctx,
                    task.get_template_env(),
                    set(),
                )
            )
            allowed = allowed_trigger_users_from_contract_file(path)
            dr = ctx.get("dag_run")
            triggering_user_name = getattr(dr, "triggering_user_name", None) if dr else None
            run_trigger_user_guard(
                dag_id=ctx["dag"].dag_id,
                triggering_user_name=triggering_user_name,
                allowed_users=allowed,
                case_insensitive=case_insensitive,
                when_triggering_user_missing=when_triggering_user_missing,
                on_unauthorized=on_unauthorized,
                log=task.log,
            )
            return f(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
