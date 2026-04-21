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
Production DAG: ``inventory_report``

A weekly inventory report that uses the custom ``CsvWriteOperator`` to emit a
CSV file.  This example shows that the shadow mechanism works with any operator
— not only TaskFlow tasks — as long as a matching ``SinkProxy`` subclass is
provided.

The shadow candidate (``inventory_report_v2``) uses ``CsvSinkProxy`` via the
``@shadow_dag`` decorator to redirect CSV writes during shadow runs.

No external connections required — inventory data is generated in memory.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from airflow.sdk import DAG, task

# Import our custom operator (installed via plugins/ directory).
# In production this would be a proper provider import.
import sys
sys.path.insert(0, str(Path(__file__).parents[2] / "plugins"))
from csv_sink_proxy import CsvWriteOperator  # noqa: E402

_OUTPUT_DIR = Path(os.environ.get("AIRFLOW_HOME", "~/airflow")).expanduser() / "reports" / "inventory"

_PRODUCTS = [
    ("SKU-001", "Widget A", "electronics"),
    ("SKU-002", "Widget B", "electronics"),
    ("SKU-003", "Gadget X", "accessories"),
    ("SKU-004", "Gadget Y", "accessories"),
    ("SKU-005", "Tool Z",   "hardware"),
]


def _inventory_rows(logical_date: datetime) -> list[dict]:
    """Generate deterministic synthetic inventory rows."""
    seed = logical_date.toordinal()
    return [
        {
            "sku": sku,
            "name": name,
            "category": category,
            "stock": (seed + i * 7) % 200,
            "reorder_needed": (seed + i * 7) % 200 < 20,
        }
        for i, (sku, name, category) in enumerate(_PRODUCTS)
    ]


@task
def prepare_inventory(**context) -> list[dict]:
    return _inventory_rows(context["logical_date"])


with DAG(
    dag_id="inventory_report",
    schedule="@weekly",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["shadow-example", "custom-sink", "production"],
    doc_md=__doc__,
) as dag:

    rows_task = prepare_inventory()

    output_path = str(_OUTPUT_DIR / "{{ run_id }}" / "inventory.csv")
    write_csv = CsvWriteOperator(
        task_id="write_csv",
        output_path=output_path,
        rows=[],     # populated dynamically via XCom in a real pipeline
    )

    rows_task >> write_csv
