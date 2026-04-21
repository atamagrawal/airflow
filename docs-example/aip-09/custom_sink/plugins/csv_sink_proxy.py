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
Custom ``SinkProxy`` — CSV operator shadow redirection.

This module shows how to subclass ``SinkProxy`` for a custom operator
(``CsvWriteOperator``) that writes rows to a CSV file.

During shadow runs the proxy:
  1. Intercepts the operator's ``output_path`` attribute.
  2. Replaces it with a path under the shadow sink root.
  3. Ensures the production CSV is never written to.

Install this file as an Airflow plugin (``$AIRFLOW_HOME/plugins/``) or drop it
anywhere on the Python path.

Usage in a DAG::

    from plugins.csv_sink_proxy import CsvWriteOperator, CsvSinkProxy

    # Register the proxy globally so the shadow scheduler picks it up:
    from airflow.shadow.sink_proxy import SinkProxy
    SinkProxy.register(CsvWriteOperator, CsvSinkProxy)

    # Or apply it per-operator in the shadow DAG:
    proxy = CsvSinkProxy()
    wrapped = proxy.wrap(my_csv_operator, shadow_ctx)
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from airflow.models import BaseOperator
from airflow.shadow.sink_proxy import ShadowContext, SinkProxy, UnsupportedSinkError

log = logging.getLogger(__name__)


class CsvWriteOperator(BaseOperator):
    """
    Minimal operator that writes a list of dicts to a CSV file.

    ``output_path`` is the production write destination.  When wrapped by
    ``CsvSinkProxy`` during a shadow run, ``output_path`` is transparently
    redirected to the shadow sink.

    In real use you would replace the body of ``execute`` with your actual
    data computation.
    """

    def __init__(self, output_path: str, rows: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.output_path = output_path
        self.rows = rows

    def execute(self, context: dict[str, Any]) -> int:
        path = Path(self.output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if not self.rows:
            log.warning("%s: no rows to write", self.task_id)
            return 0

        fieldnames = list(self.rows[0].keys())
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.rows)

        log.info("%s: wrote %d rows to %s", self.task_id, len(self.rows), path)
        return len(self.rows)


class CsvSinkProxy(SinkProxy):
    """
    Proxy implementation for ``CsvWriteOperator``.

    Redirects ``output_path`` to ``<shadow_sink_root>/<task_id>/output.csv``.
    The production path is never touched.

    Raises ``UnsupportedSinkError`` for any operator that is not a
    ``CsvWriteOperator`` so that accidentally wrapping the wrong type fails
    loudly rather than silently writing to production.
    """

    def wrap(self, operator: Any, shadow_ctx: ShadowContext) -> Any:
        if not isinstance(operator, CsvWriteOperator):
            raise UnsupportedSinkError(type(operator))

        shadow_dir = shadow_ctx.sink_root / (operator.task_id or "task")
        shadow_dir.mkdir(parents=True, exist_ok=True)
        shadow_path = shadow_dir / "output.csv"

        original_path = operator.output_path
        operator.output_path = str(shadow_path)

        log.info(
            "CsvSinkProxy: redirected %s output: %s → %s",
            operator.task_id,
            original_path,
            shadow_path,
        )
        return operator

    def resolve_output_path(self, shadow_ctx: ShadowContext, task_id: str) -> Path:
        """Return the expected CSV path for *task_id* in this shadow run."""
        return shadow_ctx.sink_root / task_id / "output.csv"
