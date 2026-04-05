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
from airflow.providers.data.contracts.contract_validate_runner import validate_contract_stats
from airflow.providers.data.contracts_decorators.decorators._python_operator_execute import (
    bind_python_decorated_callable,
)
from airflow.providers.standard.decorators.python import _PythonDecoratedOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator

OnViolation = Literal["fail", "warn"]


class _ContractValidateDecoratedOperator(_PythonDecoratedOperator):
    """
    Run the decorated callable for stats, then validate like the catalog operator.

    Same behavior as
    :class:`~airflow.providers.data.contracts.operators.contract_validate.ContractValidateOperator`.
    """

    template_fields: Sequence[str] = (
        *_PythonDecoratedOperator.template_fields,
        "dataset_urn",
        "contract_yaml_path",
    )
    custom_operator_name: str = "@task.contract_validate"

    def __init__(
        self,
        *,
        python_callable: Callable,
        catalog_conn_id: str,
        dataset_urn: str,
        contract_yaml_path: str | None = None,
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
        self.catalog_conn_id = catalog_conn_id
        self.dataset_urn = dataset_urn
        self.contract_yaml_path = contract_yaml_path
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

        stats = self.execute_callable()
        if self.show_return_value_in_logs:
            self.log.info("Done. Returned value was: %s", stats)
        else:
            self.log.info("Done. Returned value not shown")

        if not isinstance(stats, dict):
            msg = f"Callable return value must be a contract stats dict, got {type(stats).__name__}"
            raise TypeError(msg)

        ti = context["ti"]
        return validate_contract_stats(
            stats=stats,
            context=context,
            task_id=self.task_id,
            ti=ti,
            catalog_conn_id=self.catalog_conn_id,
            dataset_urn=self.dataset_urn,
            contract_yaml_path=self.contract_yaml_path,
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
