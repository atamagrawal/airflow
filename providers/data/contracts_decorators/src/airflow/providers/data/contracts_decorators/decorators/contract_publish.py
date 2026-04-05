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
from typing import TYPE_CHECKING, Any

from airflow.providers.common.compat.sdk import task_decorator_factory
from airflow.providers.data.contracts.contract_publish_runner import publish_contract_run
from airflow.providers.data.contracts_decorators.decorators._python_operator_execute import (
    bind_python_decorated_callable,
)
from airflow.providers.standard.decorators.python import _PythonDecoratedOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator


class _ContractPublishDecoratedOperator(_PythonDecoratedOperator):
    """
    Run the decorated callable for stats, then publish to the catalog.

    Same role as
    :class:`~airflow.providers.data.contracts.operators.contract_publish.ContractPublishOperator`.
    """

    template_fields: Sequence[str] = (
        *_PythonDecoratedOperator.template_fields,
        "dataset_urn",
    )
    custom_operator_name: str = "@task.contract_publish"

    def __init__(
        self,
        *,
        python_callable: Callable,
        catalog_conn_id: str,
        dataset_urn: str,
        upstream_urns: list[str] | None = None,
        update_contract_status: bool = True,
        contract_status: str = "ACTIVE",
        emit_run_facet: bool = True,
        op_args: Any = None,
        op_kwargs: Any = None,
        **kwargs,
    ) -> None:
        self.catalog_conn_id = catalog_conn_id
        self.dataset_urn = dataset_urn
        self.upstream_urns = upstream_urns or []
        self.update_contract_status = update_contract_status
        self.contract_status = contract_status
        self.emit_run_facet = emit_run_facet
        super().__init__(
            python_callable=python_callable,
            op_args=op_args,
            op_kwargs=op_kwargs,
            **kwargs,
        )

    def execute(self, context: Context) -> None:
        if self.is_async:
            msg = "Async callables are not supported for @task.contract_publish"
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

        publish_contract_run(
            context=context,
            catalog_conn_id=self.catalog_conn_id,
            dataset_urn=self.dataset_urn,
            upstream_urns=self.upstream_urns,
            stats=stats,
            update_contract_status=self.update_contract_status,
            contract_status=self.contract_status,
            emit_run_facet=self.emit_run_facet,
        )


def contract_publish_task(
    python_callable: Callable | None = None,
    *,
    multiple_outputs: bool | None = None,
    **kwargs,
) -> TaskDecorator:
    """
    Wrap a callable that returns contract stats, then publish lineage/status to the catalog.

    Matches :class:`~airflow.providers.data.contracts.operators.contract_publish.ContractPublishOperator`
    with ``stats_xcom_task_id`` replaced by the decorated function's return value.

    :param multiple_outputs: Must be ``False`` or omitted.
    """
    if multiple_outputs:
        raise ValueError("multiple_outputs is not supported for @task.contract_publish")
    return task_decorator_factory(
        python_callable=python_callable,
        multiple_outputs=multiple_outputs,
        decorated_operator_class=_ContractPublishDecoratedOperator,
        **kwargs,
    )
