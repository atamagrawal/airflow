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
"""Bind TaskFlow context so :meth:`PythonOperator.execute_callable` can run."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from airflow.providers.common.compat.sdk import AIRFLOW_V_3_0_PLUS, context_merge

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context
    from airflow.providers.standard.decorators.python import _PythonDecoratedOperator


def bind_python_decorated_callable(operator: _PythonDecoratedOperator, context: Context) -> None:
    context_merge(context, operator.op_kwargs, templates_dict=operator.templates_dict)
    operator.op_kwargs = operator.determine_kwargs(context)

    def __prepare_execution():
        if AIRFLOW_V_3_0_PLUS:
            from airflow.sdk.execution_time.callback_runner import create_executable_runner
            from airflow.sdk.execution_time.context import context_get_outlet_events

            return create_executable_runner, context_get_outlet_events(context)
        from airflow.utils.context import context_get_outlet_events  # type: ignore[import-not-found]
        from airflow.utils.operator_helpers import ExecutionCallableRunner  # type: ignore[import-not-found]

        return ExecutionCallableRunner, context_get_outlet_events(context)

    operator._PythonOperator__prepare_execution = __prepare_execution


def log_python_callable_return_value(
    logger: logging.Logger,
    result: Any,
    *,
    show_return_value: bool,
) -> None:
    """Log the decorated callable's return value (or that it was hidden)."""
    if show_return_value:
        logger.info("Done. Returned value was: %s", result)
    else:
        logger.info("Done. Returned value not shown")
