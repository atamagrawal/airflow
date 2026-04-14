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

import functools
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from airflow.providers.common.compat.sdk import task_decorator_factory
from airflow.providers.data.contracts.contract_trigger_user_runner import (
    OnUnauthorized,
    WhenTriggeringUserMissing,
    resolve_allowed_trigger_users_for_execution,
    run_trigger_user_guard_for_context,
    validate_trigger_user_guard_params,
)
from airflow.providers.data.contracts_decorators.decorators._python_operator_execute import (
    bind_python_decorated_callable,
    log_python_callable_return_value,
)
from airflow.providers.data.contracts_decorators.decorators._stackable_under_task import (
    current_task_context_and_renderer,
)
from airflow.providers.standard.decorators.python import _PythonDecoratedOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator

F = TypeVar("F", bound=Callable[..., Any])


def contract_trigger_user_guard(
    *,
    allowed_users: list[str] | None = None,
    dataset_urn: str | None = None,
    case_insensitive: bool = True,
    when_triggering_user_missing: WhenTriggeringUserMissing = "allow",
    on_unauthorized: OnUnauthorized = "fail",
) -> Callable[[F], F]:
    """
    Wrap task body callables so the trigger-user guard runs first when stacked **under** ``@task``.

    Apply **bottom-up** (this decorator directly on the function, ``@task`` outer)::

        @task
        @contract_trigger_user_guard(dataset_urn="urn:example:my_dataset")
        def my_task():
            return 1

    Template fields (``dataset_urn``) are rendered with the outer task's Jinja env.
    """
    validate_trigger_user_guard_params(
        allowed_users=allowed_users,
        contract_yaml_path=None,
        catalog_conn_id=None,
        dataset_urn=dataset_urn,
    )

    def decorator(f: F) -> F:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx, render = current_task_context_and_renderer()
            task = ctx["task"]
            r_allowed = render(allowed_users) if allowed_users is not None else None
            r_urn = render(dataset_urn) if dataset_urn is not None else None

            allowed_list = resolve_allowed_trigger_users_for_execution(
                allowed_users=r_allowed,
                contract_yaml_path=None,
                catalog_conn_id=None,
                dataset_urn=r_urn,
            )
            run_trigger_user_guard_for_context(
                ctx,
                allowed_users=allowed_list,
                case_insensitive=case_insensitive,
                when_triggering_user_missing=when_triggering_user_missing,
                on_unauthorized=on_unauthorized,
                log=task.log,
            )
            return f(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


class _ContractTriggerUserGuardDecoratedOperator(_PythonDecoratedOperator):
    """
    TaskFlow counterpart to :class:`~airflow.providers.data.contracts.operators.contract_trigger_user_guard.ContractTriggerUserGuardOperator`.

    After the guard passes, the decorated callable runs as usual and its return value is kept.
    """

    template_fields: Sequence[str] = (
        *_PythonDecoratedOperator.template_fields,
        "allowed_users",
        "dataset_urn",
    )
    custom_operator_name: str = "@task.contract_trigger_user_guard"

    def __init__(
        self,
        *,
        python_callable: Callable,
        allowed_users: list[str] | None = None,
        dataset_urn: str | None = None,
        case_insensitive: bool = True,
        when_triggering_user_missing: WhenTriggeringUserMissing = "allow",
        on_unauthorized: OnUnauthorized = "fail",
        op_args: Any = None,
        op_kwargs: Any = None,
        **kwargs,
    ) -> None:
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

        result = self.execute_callable()
        log_python_callable_return_value(
            self.log,
            result,
            show_return_value=self.show_return_value_in_logs,
        )
        return result


def contract_trigger_user_guard_task(
    python_callable: Callable | None = None,
    *,
    multiple_outputs: bool | None = None,
    allowed_users: list[str] | None = None,
    dataset_urn: str | None = None,
    case_insensitive: bool = True,
    when_triggering_user_missing: WhenTriggeringUserMissing = "allow",
    on_unauthorized: OnUnauthorized = "fail",
    **kwargs,
) -> TaskDecorator:
    """
    Restrict manual/API/UI triggers using ``DagRun.triggering_user_name`` (same rules as the operator).

    Use this factory like ``@task.contract_trigger_user_guard`` — one TaskFlow operator runs the guard,
    then your callable. ``task_id`` defaults to the function name (same as ``@task``).

    To combine a plain ``@task`` with a separate guard, stack :func:`contract_trigger_user_guard` on the
    callable (**bottom-up**: inner ``@contract_trigger_user_guard``, outer ``@task``).

    Typical DAG code sets **``dataset_urn``** only; the platform supplies the default YAML catalog
    connection. Use **``allowed_users``** for a self-contained list.
    """
    guard_kwargs = dict(
        allowed_users=allowed_users,
        dataset_urn=dataset_urn,
        case_insensitive=case_insensitive,
        when_triggering_user_missing=when_triggering_user_missing,
        on_unauthorized=on_unauthorized,
    )

    if multiple_outputs:
        raise ValueError("multiple_outputs is not supported for @task.contract_trigger_user_guard")
    merged = {**guard_kwargs, **kwargs}
    return task_decorator_factory(
        python_callable=python_callable,
        multiple_outputs=multiple_outputs,
        decorated_operator_class=_ContractTriggerUserGuardDecoratedOperator,
        **merged,
    )
