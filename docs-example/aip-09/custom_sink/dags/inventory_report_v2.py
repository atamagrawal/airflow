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
Shadow candidate DAG: ``inventory_report_v2``

Candidate for ``inventory_report`` that:

* Adds a ``days_until_stockout`` column (new field → schema divergence in report).
* Changes ``reorder_needed`` threshold from < 20 to < 30 (value divergence).

Custom ``SinkProxy``
--------------------
``CsvWriteOperator`` is not the standard ``LocalFileSinkProxy`` target — it
writes to a CSV file path, not a JSONL path.  We use ``CsvSinkProxy`` (from
``plugins/csv_sink_proxy.py``) to handle the redirection.

The ``@shadow_dag`` decorator marks this DAG as a shadow candidate.  For the
sink proxy to be applied the scheduler must have access to ``CsvSinkProxy``.
Register it by placing ``plugins/csv_sink_proxy.py`` under
``$AIRFLOW_HOME/plugins/`` (Airflow auto-discovers plugins at startup).

How ``CsvSinkProxy`` is applied
--------------------------------
The scheduler calls ``SinkProxy.wrap(operator, shadow_ctx)`` for each task in
the shadow DagRun.  The registry maps ``CsvWriteOperator`` → ``CsvSinkProxy``,
so the proxy:

1. Saves the original ``output_path``.
2. Replaces it with ``<shadow_sink_root>/<task_id>/output.csv``.
3. Returns the mutated operator.

The production CSV is never touched.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

from airflow.sdk import DAG, task
from airflow.sdk.definitions.shadow import shadow_dag

sys.path.insert(0, str(Path(__file__).parents[2] / "plugins"))
from csv_sink_proxy import CsvSinkProxy, CsvWriteOperator  # noqa: E402

_OUTPUT_DIR = Path(os.environ.get("AIRFLOW_HOME", "~/airflow")).expanduser() / "reports" / "inventory"

_PRODUCTS = [
    ("SKU-001", "Widget A", "electronics"),
    ("SKU-002", "Widget B", "electronics"),
    ("SKU-003", "Gadget X", "accessories"),
    ("SKU-004", "Gadget Y", "accessories"),
    ("SKU-005", "Tool Z",   "hardware"),
]

# Simulated average daily sales velocity per SKU (units/day).
_VELOCITY = {"SKU-001": 3, "SKU-002": 5, "SKU-003": 1, "SKU-004": 2, "SKU-005": 4}


def _inventory_rows_v2(logical_date: datetime) -> list[dict]:
    """v2: higher reorder threshold + days_until_stockout."""
    seed = logical_date.toordinal()
    rows = []
    for i, (sku, name, category) in enumerate(_PRODUCTS):
        stock = (seed + i * 7) % 200
        velocity = _VELOCITY.get(sku, 1)
        days_until_stockout = stock // velocity if velocity else 999
        rows.append(
            {
                "sku": sku,
                "name": name,
                "category": category,
                "stock": stock,
                "reorder_needed": stock < 30,           # v2: threshold 20→30
                "days_until_stockout": days_until_stockout,   # new column
            }
        )
    return rows


@task
def prepare_inventory_v2(**context) -> list[dict]:
    return _inventory_rows_v2(context["logical_date"])


class ShadowAwareCsvWriteOperator(CsvWriteOperator):
    """
    Thin subclass that respects a ``SinkProxy`` registered for its parent type.

    In a full implementation the scheduler injects the proxy automatically.
    This subclass demonstrates manual proxy application as a fallback for
    operators not yet integrated with the scheduler's auto-wrap mechanism.
    """

    def execute(self, context: dict) -> int:
        # Check whether a shadow context is active.
        from airflow.shadow.sink_proxy import ShadowContext

        shadow_ctx = ShadowContext.from_env()
        if shadow_ctx is not None:
            proxy = CsvSinkProxy()
            proxy.wrap(self, shadow_ctx)  # mutates self.output_path in-place

        return super().execute(context)


@shadow_dag(
    shadows="inventory_report",
    ttl="7d",
    divergence_alert=0.0,    # any difference is noteworthy for this report
    notify=None,
)
@DAG(
    schedule="@weekly",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["shadow-example", "custom-sink", "shadow"],
    doc_md=__doc__,
)
def inventory_report_v2():
    rows_task = prepare_inventory_v2()

    output_path = str(_OUTPUT_DIR / "{{ run_id }}" / "inventory.csv")
    write_csv = ShadowAwareCsvWriteOperator(
        task_id="write_csv",
        output_path=output_path,
        rows=[],
    )

    rows_task >> write_csv


inventory_report_v2()
