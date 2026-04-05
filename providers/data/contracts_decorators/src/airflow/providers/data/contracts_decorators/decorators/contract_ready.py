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

from airflow.providers.common.compat.sdk import context_merge, determine_kwargs, task_decorator_factory
from airflow.providers.data.contracts.contract_ready_runner import contract_ready_poke
from airflow.providers.standard.decorators.sensor import DecoratedSensorOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context, TaskDecorator


class _ContractReadyDecoratedSensor(DecoratedSensorOperator):
    """
    On each poke, resolve a dataset URN from the callable, then evaluate catalog readiness.

    Same role as
    :class:`~airflow.providers.data.contracts.sensors.contract_ready.ContractReadySensor` when the URN is dynamic.
    """

    template_fields: Sequence[str] = (
        *DecoratedSensorOperator.template_fields,
        "min_update_time",
    )
    custom_operator_name: str = "@task.contract_ready"

    def __init__(
        self,
        *,
        python_callable: Callable,
        catalog_conn_id: str,
        min_update_time: str | None = None,
        fail_on_breach: bool = True,
        op_args: Any = None,
        op_kwargs: Any = None,
        **kwargs,
    ) -> None:
        self.catalog_conn_id = catalog_conn_id
        self.min_update_time = min_update_time
        self.fail_on_breach = fail_on_breach
        super().__init__(
            python_callable=python_callable,
            op_args=op_args,
            op_kwargs=op_kwargs,
            **kwargs,
        )

    def poke(self, context: Context) -> bool:
        context_merge(context, self.op_kwargs, templates_dict=self.templates_dict)
        self.op_kwargs = determine_kwargs(self.python_callable, self.op_args, context)

        self.log.info("Poking callable: %s", str(self.python_callable))
        urn = self.python_callable(*self.op_args, **self.op_kwargs)
        if not isinstance(urn, str) or not urn.strip():
            msg = f"Callable must return a non-empty dataset URN str, got {type(urn).__name__!r}"
            raise TypeError(msg)

        return contract_ready_poke(
            catalog_conn_id=self.catalog_conn_id,
            dataset_urn=urn.strip(),
            min_update_time=self.min_update_time,
            fail_on_breach=self.fail_on_breach,
        )


def contract_ready_task(
    python_callable: Callable | None = None,
    *,
    multiple_outputs: bool | None = None,
    **kwargs,
) -> TaskDecorator:
    """
    Sensor task: each poke calls the decorated function to get a dataset URN, then checks catalog readiness.

    The callable must return a non-empty ``str`` (dataset URN). Use a constant function to mirror
    :class:`~airflow.providers.data.contracts.sensors.contract_ready.ContractReadySensor` with a fixed URN.

    :param multiple_outputs: Must be ``False`` or omitted.
    """
    if multiple_outputs:
        raise ValueError("multiple_outputs is not supported for @task.contract_ready")
    return task_decorator_factory(
        python_callable=python_callable,
        multiple_outputs=multiple_outputs,
        decorated_operator_class=_ContractReadyDecoratedSensor,
        **kwargs,
    )
