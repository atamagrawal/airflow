#
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
"""Tests for the Shadow DAG Sink Proxy layer (AIP-09)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from airflow.shadow.sink_proxy import (
    SHADOW_RUN_ENV_VAR,
    LocalFileSinkProxy,
    ShadowContext,
    UnsupportedSinkError,
)


@pytest.fixture()
def shadow_ctx(tmp_path: Path) -> ShadowContext:
    return ShadowContext(
        shadow_id="shd_test_dag_20260420",
        production_dag_id="prod_dag",
        run_id="run_001",
        sink_root=tmp_path / "shadow" / "shd_test_dag_20260420" / "run_001",
    )


class TestShadowContext:
    def test_from_env_returns_none_when_no_env_var(self):
        with patch.dict(os.environ, {}, clear=True):
            result = ShadowContext.from_env()
        assert result is None

    def test_from_env_builds_context_from_env(self, tmp_path: Path):
        env = {
            SHADOW_RUN_ENV_VAR: "shd_prod_20260420",
            "AIRFLOW_SHADOW_DAGRUN_ID": "my_run",
            "AIRFLOW_SHADOW_PROD_DAG_ID": "prod_dag",
            "AIRFLOW_HOME": str(tmp_path),
        }
        with patch.dict(os.environ, env, clear=False):
            ctx = ShadowContext.from_env()
        assert ctx is not None
        assert ctx.shadow_id == "shd_prod_20260420"
        assert ctx.run_id == "my_run"
        assert ctx.production_dag_id == "prod_dag"
        assert "shd_prod_20260420" in str(ctx.sink_root)


class TestLocalFileSinkProxy:
    def test_wrap_injects_pre_execute_hook(self, shadow_ctx: ShadowContext):
        proxy = LocalFileSinkProxy()
        operator = MagicMock()
        operator.task_id = "my_task"
        wrapped = proxy.wrap(operator, shadow_ctx)

        # Hook should be set on the operator instance
        assert hasattr(wrapped, "_pre_execute_hook")
        assert callable(wrapped._pre_execute_hook)

    def test_wrap_stores_output_path(self, shadow_ctx: ShadowContext):
        proxy = LocalFileSinkProxy()
        operator = MagicMock()
        operator.task_id = "task_a"
        wrapped = proxy.wrap(operator, shadow_ctx)

        assert hasattr(wrapped, "__shadow_output_path__")
        assert "task_a" in wrapped.__shadow_output_path__
        assert "output.jsonl" in wrapped.__shadow_output_path__

    def test_pre_execute_hook_injects_context(self, shadow_ctx: ShadowContext, tmp_path: Path):
        proxy = LocalFileSinkProxy()
        operator = MagicMock()
        operator.task_id = "task_b"
        wrapped = proxy.wrap(operator, shadow_ctx)

        context: dict = {}
        wrapped._pre_execute_hook(context)
        assert "shadow_output_path" in context
        assert "shadow_sink_root" in context

    def test_pre_execute_calls_original_hook(self, shadow_ctx: ShadowContext):
        original_hook = MagicMock()
        operator = MagicMock()
        operator.task_id = "task_c"
        operator._pre_execute_hook = original_hook

        proxy = LocalFileSinkProxy()
        wrapped = proxy.wrap(operator, shadow_ctx)

        context: dict = {}
        wrapped._pre_execute_hook(context)
        original_hook.assert_called_once_with(context)

    def test_resolve_output_path(self, shadow_ctx: ShadowContext):
        proxy = LocalFileSinkProxy()
        path = proxy.resolve_output_path(shadow_ctx, "task_x")
        assert path.name == "output.jsonl"
        assert "task_x" in str(path)


class TestUnsupportedSinkError:
    def test_error_message_contains_operator_type(self):
        class MyUnknownOperator:
            pass

        err = UnsupportedSinkError(MyUnknownOperator)
        assert "MyUnknownOperator" in str(err)
        assert "shadow-safe" in str(err)
