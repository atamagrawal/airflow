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
from airflow.providers.data.contracts.contract_validate_runner import (
    validate_contract_stats,
)
from airflow.providers.data.contracts.hooks.local_yaml import YamlDataContractHook
from airflow.providers.data.contracts_decorators.decorators._python_operator_execute import (
    bind_python_decorated_callable,
    log_python_callable_return_value,
)
from airflow.providers.data.contracts_decorators.decorators._stackable_under_task import (
    current_task_context_and_renderer,
    expect_contract_stats_dict,
)
from airflow.providers.standard.decorators.python import _PythonDecoratedOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator

OnViolation = Literal["fail", "warn"]

F = TypeVar("F", bound=Callable[..., Any])


def contract_validate(
    *,
    dataset_urn: str,
    validate_schema: bool = True,
    validate_freshness: bool = True,
    validate_completeness: bool = True,
    validate_sla: bool = True,
    on_schema_violation: OnViolation = "fail",
    on_freshness_violation: OnViolation = "warn",
    on_completeness_violation: OnViolation = "fail",
    on_sla_violation: OnViolation = "warn",
    report_breach_to_catalog: bool = True,
    result_xcom_key: str = "contract_result",
) -> Callable[[F], F]:
    """
    Wrap a stats callable when stacked **under** plain ``@task`` (apply bottom-up).

    The inner function returns the same stats dict as ``@task.contract_validate``; this wrapper runs
    :func:`~airflow.providers.data.contracts.contract_validate_runner.validate_contract_stats` after it.
    String parameters are rendered with the outer task's Jinja environment.
    """

    def decorator(f: F) -> F:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> dict:
            ctx, render = current_task_context_and_renderer()
            r_dataset_urn = render(dataset_urn)

            stats = expect_contract_stats_dict(f(*args, **kwargs))
            ti = ctx["ti"]
            outer_task = ctx["task"]
            return validate_contract_stats(
                stats=stats,
                context=ctx,
                task_id=outer_task.task_id,
                ti=ti,
                catalog_conn_id=YamlDataContractHook.default_conn_name,
                dataset_urn=r_dataset_urn,
                contract_yaml_path=None,
                validate_schema_flag=validate_schema,
                validate_freshness_flag=validate_freshness,
                validate_completeness_flag=validate_completeness,
                validate_sla_flag=validate_sla,
                on_schema_violation=on_schema_violation,
                on_freshness_violation=on_freshness_violation,
                on_completeness_violation=on_completeness_violation,
                on_sla_violation=on_sla_violation,
                report_breach_to_catalog=report_breach_to_catalog,
                result_xcom_key=result_xcom_key,
            )

        return wrapper  # type: ignore[return-value]

    return decorator


class _ContractValidateDecoratedOperator(_PythonDecoratedOperator):
    """
    Run the decorated callable for stats, then validate like the catalog operator.

    Same behavior as
    :class:`~airflow.providers.data.contracts.operators.contract_validate.ContractValidateOperator`.
    """

    template_fields: Sequence[str] = (
        *_PythonDecoratedOperator.template_fields,
        "dataset_urn",
    )
    custom_operator_name: str = "@task.contract_validate"

    def __init__(
        self,
        *,
        python_callable: Callable,
        dataset_urn: str,
        validate_schema: bool = True,
        validate_freshness: bool = True,
        validate_completeness: bool = True,
        validate_sla: bool = True,
        on_schema_violation: OnViolation = "fail",
        on_freshness_violation: OnViolation = "warn",
        on_completeness_violation: OnViolation = "fail",
        on_sla_violation: OnViolation = "warn",
        report_breach_to_catalog: bool = True,
        result_xcom_key: str = "contract_result",
        op_args: Any = None,
        op_kwargs: Any = None,
        **kwargs,
    ) -> None:
        self.dataset_urn = dataset_urn
        self.validate_schema_flag = validate_schema
        self.validate_freshness = validate_freshness
        self.validate_completeness = validate_completeness
        self.validate_sla_flag = validate_sla
        self.on_schema_violation = on_schema_violation
        self.on_freshness_violation = on_freshness_violation
        self.on_completeness_violation = on_completeness_violation
        self.on_sla_violation = on_sla_violation
        self.report_breach_to_catalog = report_breach_to_catalog
        self.result_xcom_key = result_xcom_key
        super().__init__(
            python_callable=python_callable,
            op_args=op_args,
            op_kwargs=op_kwargs,
            **kwargs,
        )

    def execute(self, context: Context) -> dict:
        if self.is_async:
            msg = "Async callables are not supported for @task.contract_validate"
            raise TypeError(msg)

        bind_python_decorated_callable(self, context)

        stats = expect_contract_stats_dict(self.execute_callable())
        log_python_callable_return_value(
            self.log,
            stats,
            show_return_value=self.show_return_value_in_logs,
        )

        ti = context["ti"]
        return validate_contract_stats(
            stats=stats,
            context=context,
            task_id=self.task_id,
            ti=ti,
            catalog_conn_id=YamlDataContractHook.default_conn_name,
            dataset_urn=self.dataset_urn,
            contract_yaml_path=None,
            validate_schema_flag=self.validate_schema_flag,
            validate_freshness_flag=self.validate_freshness,
            validate_completeness_flag=self.validate_completeness,
            validate_sla_flag=self.validate_sla_flag,
            on_schema_violation=self.on_schema_violation,
            on_freshness_violation=self.on_freshness_violation,
            on_completeness_violation=self.on_completeness_violation,
            on_sla_violation=self.on_sla_violation,
            report_breach_to_catalog=self.report_breach_to_catalog,
            result_xcom_key=self.result_xcom_key,
        )


def contract_validate_task(
    python_callable: Callable | None = None,
    *,
    multiple_outputs: bool | None = None,
    **kwargs,
) -> TaskDecorator:
    """
    Wrap a Python callable that returns contract stats into a single validated task.

    The callable must return the same mapping shape as
    :class:`~airflow.providers.data.contracts.operators.contract_validate.ContractValidateOperator`
    expects from XCom (for example ``row_count``, ``schema``, ``data_as_of``).

    For plain ``@task`` with validation in a separate layer, stack :func:`contract_validate` on the
    callable (inner decorator, ``@task`` outer).

    Install ``apache-airflow-providers-data-contracts-decorators`` (and the base data-contracts provider).

    :param python_callable: Function to decorate.
    :param multiple_outputs: Must be ``False`` or omitted; contract validation returns one XCom payload.
    """
    if multiple_outputs:
        raise ValueError("multiple_outputs is not supported for @task.contract_validate")
    return task_decorator_factory(
        python_callable=python_callable,
        multiple_outputs=multiple_outputs,
        decorated_operator_class=_ContractValidateDecoratedOperator,
        **kwargs,
    )
