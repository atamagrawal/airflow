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
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from airflow.providers.common.compat.sdk import task_decorator_factory
from airflow.providers.data.contracts.contract_breach_runner import run_contract_breach_guard
from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook
from airflow.providers.data.contracts_decorators.decorators._python_operator_execute import (
    bind_python_decorated_callable,
    log_python_callable_return_value,
)
from airflow.providers.data.contracts_decorators.decorators._stackable_under_task import (
    current_task_context_and_renderer,
    expect_dataset_urn_list,
)
from airflow.providers.standard.decorators.python import _PythonDecoratedOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator

OnBreach = Literal["fail", "skip", "warn"]

F = TypeVar("F", bound=Callable[..., Any])


def contract_breach_guard(
    *,
    on_breach: OnBreach = "fail",
    override_var: str | None = None,
) -> Callable[[F], F]:
    """
    Wrap a callable that returns ``list[str]`` dataset URNs when stacked **under** plain ``@task``.

    Runs :func:`~airflow.providers.data.contracts.contract_breach_runner.run_contract_breach_guard`
    after the callable. Renders ``override_var`` with the outer task's Jinja environment.
    """

    def decorator(f: F) -> F:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> None:
            ctx, render = current_task_context_and_renderer()
            task = ctx["task"]
            r_override = render(override_var) if override_var is not None else None
            urns = expect_dataset_urn_list(f(*args, **kwargs))

            run_contract_breach_guard(
                catalog_conn_id=YamlDataContractHook.default_conn_name,
                dataset_urns=urns,
                on_breach=on_breach,
                override_var=r_override,
                log=task.log,
            )

        return wrapper  # type: ignore[return-value]

    return decorator


class _ContractBreachGuardDecoratedOperator(_PythonDecoratedOperator):
    """
    Run the decorated callable for dataset URNs, then enforce breach policy.

    Same role as
    :class:`~airflow.providers.data.contracts.operators.contract_breach_guard.ContractBreachGuardOperator`.
    """

    template_fields: Sequence[str] = (*_PythonDecoratedOperator.template_fields,)
    custom_operator_name: str = "@task.contract_breach_guard"

    def __init__(
        self,
        *,
        python_callable: Callable,
        on_breach: OnBreach = "fail",
        override_var: str | None = None,
        op_args: Any = None,
        op_kwargs: Any = None,
        **kwargs,
    ) -> None:
        self.on_breach = on_breach
        self.override_var = override_var
        super().__init__(
            python_callable=python_callable,
            op_args=op_args,
            op_kwargs=op_kwargs,
            **kwargs,
        )

    def execute(self, context: Context) -> None:
        if self.is_async:
            msg = "Async callables are not supported for @task.contract_breach_guard"
            raise TypeError(msg)

        bind_python_decorated_callable(self, context)

        urns = expect_dataset_urn_list(self.execute_callable())
        log_python_callable_return_value(
            self.log,
            urns,
            show_return_value=self.show_return_value_in_logs,
        )

        run_contract_breach_guard(
            catalog_conn_id=YamlDataContractHook.default_conn_name,
            dataset_urns=urns,
            on_breach=self.on_breach,
            override_var=self.override_var,
            log=self.log,
        )


def contract_breach_guard_task(
    python_callable: Callable | None = None,
    *,
    multiple_outputs: bool | None = None,
    **kwargs,
) -> TaskDecorator:
    """
    Wrap a callable that returns ``list[str]`` dataset URNs, then run the breach guard.

    Matches :class:`~airflow.providers.data.contracts.operators.contract_breach_guard.ContractBreachGuardOperator`
    with ``dataset_urns`` supplied by the callable.

    For plain ``@task``, stack :func:`contract_breach_guard` on the callable (inner, ``@task`` outer).

    :param multiple_outputs: Must be ``False`` or omitted.
    """
    if multiple_outputs:
        raise ValueError("multiple_outputs is not supported for @task.contract_breach_guard")
    return task_decorator_factory(
        python_callable=python_callable,
        multiple_outputs=multiple_outputs,
        decorated_operator_class=_ContractBreachGuardDecoratedOperator,
        **kwargs,
    )
