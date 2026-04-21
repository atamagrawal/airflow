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
Shadow candidate DAG: ``orders_daily_v2``

Candidate ETL that fixes two bugs found in ``orders_daily`` (v1):

  1. **Discount not applied** — v2 applies ``discount_pct`` before converting to USD.
  2. **Customer PII** — v2 hashes ``customer_id`` with a HMAC so raw IDs
     are never stored in the output layer.

Intentional divergences (visible in the comparison report):

* ``total_usd`` differs for any order with discount > 0 (value divergence).
* ``customer`` column contains a 16-char hex digest instead of raw IDs
  (value divergence, no schema change).
* A new column ``discount_applied`` is added (schema divergence — new field).

Using ``@shadow_dag``
---------------------
Stacking ``@shadow_dag`` above the ``@DAG`` factory:

    @shadow_dag(shadows="orders_daily", ttl="14d", divergence_alert=0.10)
    @DAG(...)
    def orders_daily_v2():
        ...

This injects a ``__shadow__:<json>`` tag into the DAG object.  The DAG
Processor detects this tag in ``collection.py`` and calls
``ShadowDagService.create()`` automatically on the first parse — no manual
``airflow shadow create`` needed.

Shadow-aware output pattern
---------------------------
``normalize_orders_v2`` checks ``context["shadow_output_path"]``, which is
injected by ``LocalFileSinkProxy`` during shadow runs.  When ``None`` (normal
production run of this DAG file, e.g. during development) it falls back to a
local path so the DAG can still run standalone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
from datetime import datetime
from pathlib import Path

from airflow.sdk import DAG, task
from airflow.sdk.definitions.shadow import shadow_dag

log = logging.getLogger(__name__)

_OUTPUT_BASE = (
    Path(os.environ.get("AIRFLOW_HOME", "~/airflow")).expanduser()
    / "prod_output"
    / "orders_daily"
)

_REGIONS = ["us-east", "us-west", "eu-central", "ap-south"]
_STATUSES = ["completed", "completed", "completed", "refunded", "pending"]
_FX = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}

# HMAC key for customer_id hashing.  In a real environment inject via an
# Airflow Variable or Secret Backend.
_HASH_KEY = os.environ.get("SHADOW_HASH_KEY", "dev-only-key").encode()


def _synthetic_orders(logical_date: datetime, n: int = 50) -> list[dict]:
    """Same seed as v1 — identical raw data, different normalisation."""
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


def _hash_customer(customer_id: str) -> str:
    return hashlib.blake2b(customer_id.encode(), key=_HASH_KEY, digest_size=8).hexdigest()


@task
def extract_orders(**context) -> list[dict]:
    logical_date: datetime = context["logical_date"]
    orders = _synthetic_orders(logical_date)
    log.info("extract_orders: %d raw orders for %s", len(orders), logical_date.date())
    return orders


@task
def normalize_orders_v2(raw_orders: list[dict], **context) -> int:
    """
    v2 normalisation (fixes applied):
    - Applies discount_pct before FX conversion.
    - Hashes customer_id with BLAKE2b (PII fix).
    - Adds discount_applied boolean flag.

    Writes to ``context["shadow_output_path"]`` when running as a shadow task,
    otherwise falls back to a local path for standalone development runs.
    """
    run_id: str = context["run_id"]
    shadow_output_path: str | None = context.get("shadow_output_path")

    if shadow_output_path:
        output_path = Path(shadow_output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fallback_dir = _OUTPUT_BASE / run_id / "normalize_v2"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        output_path = fallback_dir / "output.jsonl"

    rows = []
    for o in raw_orders:
        fx = _FX.get(o["currency"], 1.0)
        discount_multiplier = 1.0 - (o["discount_pct"] / 100.0)
        total = round(o["amount"] * discount_multiplier * fx, 2)
        rows.append(
            {
                "order_id": o["order_id"],
                "customer": _hash_customer(o["customer_id"]),   # PII fix
                "region": o["region"].upper(),
                "total_usd": total,                              # discount now applied
                "status": o["status_code"],
                "discount_applied": o["discount_pct"] > 0,      # new field
            }
        )

    with output_path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    log.info(
        "normalize_orders_v2: %d rows → %s (shadow=%s)",
        len(rows),
        output_path,
        shadow_output_path is not None,
    )
    return len(rows)


@task
def quality_check(row_count: int) -> int:
    if row_count == 0:
        raise ValueError("quality_check: no rows produced — pipeline aborted")
    log.info("quality_check: passed (%d rows)", row_count)
    return row_count


@task
def write_warehouse(row_count: int, **context) -> None:
    log.info(
        "write_warehouse: %d rows committed for run %s (warehouse write simulated)",
        row_count,
        context["run_id"],
    )


@shadow_dag(
    shadows="orders_daily",
    ttl="14d",
    divergence_alert=0.10,    # alert on > 10 % row-count difference
    notify=None,              # set to "oncall@example.com" for e-mail alerts
)
@DAG(
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["shadow-example", "etl", "shadow"],
    doc_md=__doc__,
)
def orders_daily_v2():
    raw = extract_orders()
    normalised = normalize_orders_v2(raw)
    checked = quality_check(normalised)
    write_warehouse(checked)


orders_daily_v2()
