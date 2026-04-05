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
from airflow.providers.data.contracts.contract_breach_runner import run_contract_breach_guard
from airflow.providers.data.contracts_decorators.decorators._python_operator_execute import (
    bind_python_decorated_callable,
)
from airflow.providers.standard.decorators.python import _PythonDecoratedOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator

OnBreach = Literal["fail", "skip", "warn"]


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
        catalog_conn_id: str,
        on_breach: OnBreach = "fail",
        override_var: str | None = None,
        op_args: Any = None,
        op_kwargs: Any = None,
        **kwargs,
    ) -> None:
        self.catalog_conn_id = catalog_conn_id
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

        urns = self.execute_callable()
        if self.show_return_value_in_logs:
            self.log.info("Done. Returned value was: %s", urns)
        else:
            self.log.info("Done. Returned value not shown")

        if not isinstance(urns, list) or not all(isinstance(u, str) for u in urns):
            msg = f"Callable must return a list[str] of dataset URNs, got {type(urns).__name__}"
            raise TypeError(msg)

        run_contract_breach_guard(
            catalog_conn_id=self.catalog_conn_id,
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
