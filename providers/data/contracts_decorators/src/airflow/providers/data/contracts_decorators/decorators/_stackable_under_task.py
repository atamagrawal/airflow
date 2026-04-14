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
"""Helpers for stackable contract decorators that run under plain ``@task``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


def current_task_context_and_renderer() -> tuple[Mapping[str, Any], Callable[[Any], Any]]:
    """
    Return the Airflow task context and a Jinja renderer bound to the **outer** ``@task``.

    Stackable wrappers call this at runtime so ``{{ ds }}`` and other templates resolve like
    template fields on a normal operator.
    """
    from airflow.sdk import get_current_context

    ctx = get_current_context()
    task = ctx["task"]
    jinja_env = task.get_template_env()

    def render(value: Any) -> Any:
        return task.render_template(value, ctx, jinja_env, set())

    return ctx, render


def expect_contract_stats_dict(value: Any) -> dict:
    if not isinstance(value, dict):
        msg = f"Callable return value must be a contract stats dict, got {type(value).__name__}"
        raise TypeError(msg)
    return value


def expect_dataset_urn_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(u, str) for u in value):
        msg = f"Callable must return a list[str] of dataset URNs, got {type(value).__name__}"
        raise TypeError(msg)
    return value


def expect_non_empty_dataset_urn(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"Callable must return a non-empty dataset URN str, got {type(value).__name__!r}"
        raise TypeError(msg)
    return value.strip()
