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

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal

from airflow.providers.common.compat.sdk import task_decorator_factory
from airflow.providers.data.contracts.contract_trigger_user_runner import (
    allowed_trigger_users_from_contract_file,
    run_trigger_user_guard,
)
from airflow.providers.data.contracts_decorators.decorators._python_operator_execute import (
    bind_python_decorated_callable,
)
from airflow.providers.standard.decorators.python import _PythonDecoratedOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator

WhenTriggeringUserMissing = Literal["allow", "fail", "skip"]
OnUnauthorized = Literal["fail", "skip", "warn", "pause_dag"]


class _ContractTriggerUserGuardDecoratedOperator(_PythonDecoratedOperator):
    """
    Enforce triggering-user policy from an allow-list or from contract YAML.

    Same role as
    :class:`~airflow.providers.data.contracts.operators.contract_trigger_user_guard.ContractTriggerUserGuardOperator`.

    * If ``contract_yaml_path`` is set, the decorated callable is the task body; allow-list
      comes from ``allowed_trigger_users`` in that file.
    * Otherwise the callable must return a non-empty ``list[str]`` of allowed users (guard-only
      task).
    """

    template_fields: Sequence[str] = (
        *_PythonDecoratedOperator.template_fields,
        "contract_yaml_path",
    )
    custom_operator_name: str = "@task.contract_trigger_user_guard"

    def __init__(
        self,
        *,
        python_callable: Callable,
        contract_yaml_path: str | None = None,
        case_insensitive: bool = True,
        when_triggering_user_missing: WhenTriggeringUserMissing = "allow",
        on_unauthorized: OnUnauthorized = "fail",
        op_args: Any = None,
        op_kwargs: Any = None,
        **kwargs,
    ) -> None:
        if contract_yaml_path is not None and not str(contract_yaml_path).strip():
            msg = "contract_yaml_path must be a non-empty string when set"
            raise ValueError(msg)
        self.contract_yaml_path = contract_yaml_path
        self.case_insensitive = case_insensitive
        self.when_triggering_user_missing = when_triggering_user_missing
        self.on_unauthorized = on_unauthorized
        super().__init__(
            python_callable=python_callable,
            op_args=op_args,
            op_kwargs=op_kwargs,
            **kwargs,
        )

    def execute(self, context: Context) -> Any:
        if self.is_async:
            msg = "Async callables are not supported for @task.contract_trigger_user_guard"
            raise TypeError(msg)

        bind_python_decorated_callable(self, context)

        if self.contract_yaml_path:
            allowed = allowed_trigger_users_from_contract_file(str(self.contract_yaml_path))
            dr = context.get("dag_run")
            triggering_user_name = getattr(dr, "triggering_user_name", None) if dr else None
            run_trigger_user_guard(
                dag_id=context["dag"].dag_id,
                triggering_user_name=triggering_user_name,
                allowed_users=allowed,
                case_insensitive=self.case_insensitive,
                when_triggering_user_missing=self.when_triggering_user_missing,
                on_unauthorized=self.on_unauthorized,
                log=self.log,
            )
            result = self.execute_callable()
            if self.show_return_value_in_logs:
                self.log.info("Done. Returned value was: %s", result)
            else:
                self.log.info("Done. Returned value not shown")
            return result

        allowed = self.execute_callable()
        if self.show_return_value_in_logs:
            self.log.info("Done. Returned value was: %s", allowed)
        else:
            self.log.info("Done. Returned value not shown")

        if not isinstance(allowed, list) or not allowed or not all(isinstance(u, str) for u in allowed):
            msg = f"Callable must return a non-empty list[str] of allowed users, got {type(allowed).__name__}"
            raise TypeError(msg)

        dr = context.get("dag_run")
        triggering_user_name = getattr(dr, "triggering_user_name", None) if dr else None
        run_trigger_user_guard(
            dag_id=context["dag"].dag_id,
            triggering_user_name=triggering_user_name,
            allowed_users=list(allowed),
            case_insensitive=self.case_insensitive,
            when_triggering_user_missing=self.when_triggering_user_missing,
            on_unauthorized=self.on_unauthorized,
            log=self.log,
        )
        return None


def contract_trigger_user_guard_task(
    python_callable: Callable | None = None,
    *,
    multiple_outputs: bool | None = None,
    **kwargs,
) -> TaskDecorator:
    """
    Enforce ``DagRun.triggering_user_name`` against an allow-list.

    Without ``contract_yaml_path``, the callable must return ``list[str]`` (allowed user names).

    With ``contract_yaml_path``, the file must define ``allowed_trigger_users`` and the callable
    is normal task code whose return value is passed through (use ``with_contract_trigger_user_from_yaml``
    to stack the check on a plain ``@task`` instead).
    """
    if multiple_outputs:
        raise ValueError("multiple_outputs is not supported for @task.contract_trigger_user_guard")
    return task_decorator_factory(
        python_callable=python_callable,
        multiple_outputs=multiple_outputs,
        decorated_operator_class=_ContractTriggerUserGuardDecoratedOperator,
        **kwargs,
    )
