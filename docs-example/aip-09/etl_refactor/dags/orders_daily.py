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
Production DAG: ``orders_daily``

Daily orders ETL pipeline (v1 — legacy normalization).

Pipeline:
  extract_orders  →  normalize_orders  →  quality_check  →  write_warehouse

``normalize_orders`` produces the JSONL output that ``ComparisonEngine`` diffs
against ``orders_daily_v2`` shadow runs.

Output schema (v1):
  order_id    str     unique order identifier
  customer    str     customer identifier (raw — not anonymised in v1)
  region      str     upper-cased region code
  total_usd   float   order total in USD, rounded to 2 dp
  status      str     "completed" | "refunded" | "pending"
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path

from airflow.sdk import DAG, task

log = logging.getLogger(__name__)

_OUTPUT_BASE = (
    Path(os.environ.get("AIRFLOW_HOME", "~/airflow")).expanduser()
    / "prod_output"
    / "orders_daily"
)

_REGIONS = ["us-east", "us-west", "eu-central", "ap-south"]
_STATUSES = ["completed", "completed", "completed", "refunded", "pending"]


def _synthetic_orders(logical_date: datetime, n: int = 50) -> list[dict]:
    """Generate deterministic synthetic order records."""
    seed = logical_date.toordinal()
    rng = random.Random(seed)
    return [
        {
            "order_id": f"ORD-{seed}-{i:04d}",
            "customer_id": f"cust_{rng.randint(1, 20):03d}",
            "region": rng.choice(_REGIONS),
            "amount": round(rng.uniform(5.0, 500.0), 2),
            "currency": rng.choice(["USD", "EUR", "GBP"]),
            "status_code": rng.choice(_STATUSES),
            "discount_pct": rng.choice([0, 5, 10, 15]),
        }
        for i in range(n)
    ]


_FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}


@task
def extract_orders(**context) -> list[dict]:
    logical_date: datetime = context["logical_date"]
    orders = _synthetic_orders(logical_date)
    log.info("extract_orders: %d raw orders for %s", len(orders), logical_date.date())
    return orders


@task
def normalize_orders(raw_orders: list[dict], **context) -> int:
    """
    v1 normalisation:
    - Converts all amounts to USD (via fixed FX rates).
    - Upper-cases region.
    - Renames status_code → status.
    - Does NOT anonymise customer_id (v2 will fix this).
    - Does NOT apply discount (oversight caught by shadow run).

    Writes output to ``_OUTPUT_BASE/<run_id>/normalize/output.jsonl``.
    """
    run_id: str = context["run_id"]
    output_dir = _OUTPUT_BASE / run_id / "normalize"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "output.jsonl"

    rows = []
    for o in raw_orders:
        fx = _FX.get(o["currency"], 1.0)
        rows.append(
            {
                "order_id": o["order_id"],
                "customer": o["customer_id"],          # v1: raw, not anonymised
                "region": o["region"].upper(),
                "total_usd": round(o["amount"] * fx, 2),  # v1: discount not applied
                "status": o["status_code"],
            }
        )

    with output_path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    log.info("normalize_orders: %d rows → %s", len(rows), output_path)
    return len(rows)


@task
def quality_check(row_count: int) -> int:
    if row_count == 0:
        raise ValueError("quality_check: no rows produced — pipeline aborted")
    log.info("quality_check: passed (%d rows)", row_count)
    return row_count


@task
def write_warehouse(row_count: int, **context) -> None:
    """Simulates writing to the data warehouse (no-op in this example)."""
    log.info(
        "write_warehouse: %d rows committed for run %s (warehouse write simulated)",
        row_count,
        context["run_id"],
    )


with DAG(
    dag_id="orders_daily",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["shadow-example", "etl", "production"],
    doc_md=__doc__,
) as _:
    raw = extract_orders()
    normalised = normalize_orders(raw)
    checked = quality_check(normalised)
    write_warehouse(checked)
