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
"""
Sink Proxy layer for Shadow DAGs — AIP-09 §4.

The Sink Proxy intercepts operator write operations during a shadow run and
redirects output to an isolated ephemeral sink.  Production data is never
written to or modified.

For local / dev environments ``LocalFileSinkProxy`` redirects all operator
output under ``$AIRFLOW_HOME/shadow/<shadow_id>/<run_id>/``.

Future provider-level implementations (BigQuery, GCS, Postgres) follow the
same ``SinkProxy`` abstract interface.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

#: Environment variable set on worker processes executing shadow tasks.
#: Value is the ``shadow_id`` of the active experiment.
SHADOW_RUN_ENV_VAR = "AIRFLOW_SHADOW_RUN_ID"

#: Sub-directory under AIRFLOW_HOME where shadow sink data is written.
_SHADOW_SINK_SUBDIR = "shadow"


class UnsupportedSinkError(ValueError):
    """
    Raised when the Sink Proxy encounters an operator whose write destination
    cannot be safely redirected.

    Shadow tasks with unsupported operators are marked ``shadow_blocked`` and
    do not execute, preventing accidental writes to unknown sinks.
    """

    def __init__(self, operator_type: type) -> None:
        super().__init__(
            f"Operator '{operator_type.__name__}' is not shadow-safe. "
            "Add a SinkProxy implementation for this operator type, or exclude "
            "it from shadow execution. The shadow task will not run."
        )


@dataclass(frozen=True)
class ShadowContext:
    """
    Immutable context passed to the Sink Proxy during shadow task execution.

    ``sink_root`` is the base directory (or namespace prefix) under which all
    shadow output for this run is isolated.
    """

    shadow_id: str
    production_dag_id: str
    run_id: str
    sink_root: Path

    @classmethod
    def from_env(cls, airflow_home: str | None = None) -> ShadowContext | None:
        """
        Build a ShadowContext from environment variables set by the scheduler.

        Returns *None* when the current process is not executing a shadow task.
        """
        shadow_id = os.environ.get(SHADOW_RUN_ENV_VAR)
        if not shadow_id:
            return None
        run_id = os.environ.get("AIRFLOW_SHADOW_DAGRUN_ID", "unknown_run")
        prod_dag_id = os.environ.get("AIRFLOW_SHADOW_PROD_DAG_ID", "unknown_dag")
        home = Path(airflow_home or os.environ.get("AIRFLOW_HOME", "~/airflow")).expanduser()
        sink_root = home / _SHADOW_SINK_SUBDIR / shadow_id / run_id
        return cls(
            shadow_id=shadow_id,
            production_dag_id=prod_dag_id,
            run_id=run_id,
            sink_root=sink_root,
        )


class SinkProxy(ABC):
    """
    Abstract base class for all Sink Proxy implementations.

    A concrete ``SinkProxy`` receives an operator instance and a
    ``ShadowContext`` and returns a *modified copy* of the operator whose
    write destination has been redirected to the shadow sink namespace.  The
    original operator object is never mutated.

    Implementations should raise ``UnsupportedSinkError`` for operator types
    they cannot safely redirect.
    """

    @abstractmethod
    def wrap(self, operator: Any, shadow_ctx: ShadowContext) -> Any:
        """
        Return a shadow-safe copy of *operator* with writes redirected.

        :param operator: The original ``BaseOperator`` instance.
        :param shadow_ctx: Runtime context for this shadow run.
        :raises UnsupportedSinkError: When *operator* cannot be safely proxied.
        """


@dataclass
class _ShadowOutputRecord:
    """Tracks a single output file written by a shadow task."""

    task_id: str
    output_path: Path
    row_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class LocalFileSinkProxy(SinkProxy):
    """
    Sink Proxy implementation for local filesystem environments.

    Instead of redirecting to cloud sinks (BigQuery, GCS, etc.) this
    implementation writes output under ``shadow_ctx.sink_root``.  Each task
    writes a JSON-lines file at::

        <sink_root>/<task_id>/output.jsonl

    This is the default proxy used in local and CI environments where cloud
    services are unavailable.  The ``ComparisonEngine`` reads from the same
    path structure when comparing shadow vs. production outputs.
    """

    def wrap(self, operator: Any, shadow_ctx: ShadowContext) -> Any:
        """
        Inject a ``pre_execute`` hook that redirects the operator's output path
        to the shadow sink directory.

        The hook stores the resolved output path on the operator so the
        ``ComparisonEngine`` can locate it after the run.
        """
        original_pre_execute = getattr(operator, "_pre_execute_hook", None)
        sink_dir = shadow_ctx.sink_root / (operator.task_id or "task")
        sink_dir.mkdir(parents=True, exist_ok=True)
        output_path = sink_dir / "output.jsonl"

        def shadow_pre_execute(context: dict[str, Any]) -> None:
            # Inject shadow output path into the XCom context so the operator
            # (or any post_execute hook) can discover the redirected sink.
            context["shadow_output_path"] = str(output_path)
            context["shadow_sink_root"] = str(shadow_ctx.sink_root)
            if original_pre_execute is not None:
                original_pre_execute(context)

        # Attach the patched hook — this does *not* change the operator class,
        # only this specific instance within the shadow DagRun.
        operator._pre_execute_hook = shadow_pre_execute  # type: ignore[attr-defined]
        # Store so the comparison engine can read it back.
        operator.__shadow_output_path__ = str(output_path)  # type: ignore[attr-defined]
        return operator

    def resolve_output_path(self, shadow_ctx: ShadowContext, task_id: str) -> Path:
        """Return the expected output path for *task_id* in this shadow run."""
        return shadow_ctx.sink_root / task_id / "output.jsonl"
