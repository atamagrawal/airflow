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
Production DAG: ``daily_summary``

Simulates a simple daily analytics pipeline:
  1. ``extract`` — returns a list of raw event records.
  2. ``transform`` — normalises events into summary rows and writes them
     to a JSONL file under ``$AIRFLOW_HOME/prod_output/daily_summary/``.
  3. ``load`` — receives the row count and logs a completion message.

The shadow candidate (``daily_summary_v2``) mirrors this structure with a
modified transformation and is registered via ``@shadow_dag``.  The
``ComparisonEngine`` reads both JSONL outputs and produces a comparison report.

No external connections required — all data is generated in memory.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from airflow.sdk import DAG, task

log = logging.getLogger(__name__)

# Production output goes here so the ComparisonEngine can find it.
_PROD_OUTPUT_DIR = Path(os.environ.get("AIRFLOW_HOME", "~/airflow")).expanduser() / "prod_output" / "daily_summary"


def _raw_events(logical_date: datetime) -> list[dict]:
    """Generate deterministic synthetic events for the given date."""
    base = logical_date.toordinal() % 100
    return [
        {"event_id": f"evt_{base + i:04d}", "user_id": f"u{(i % 10) + 1}", "amount": round(10.0 + i * 1.5, 2)}
        for i in range(20)
    ]


@task
def extract(**context) -> list[dict]:
    logical_date: datetime = context["logical_date"]
    rows = _raw_events(logical_date)
    log.info("extract: produced %d raw events for %s", len(rows), logical_date.date())
    return rows


@task
def transform(raw_events: list[dict], **context) -> int:
    """
    Normalise raw events into summary rows.

    Production algorithm v1: group by user_id, sum amount.
    Writes output to ``_PROD_OUTPUT_DIR/<run_id>/transform/output.jsonl``.
    """
    run_id: str = context["run_id"]
    output_dir = _PROD_OUTPUT_DIR / run_id / "transform"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "output.jsonl"

    from collections import defaultdict

    totals: dict[str, float] = defaultdict(float)
    for evt in raw_events:
        totals[evt["user_id"]] += evt["amount"]

    rows = [{"user_id": uid, "total_amount": round(total, 2)} for uid, total in sorted(totals.items())]

    with output_path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    log.info("transform: wrote %d summary rows to %s", len(rows), output_path)
    return len(rows)


@task
def load(row_count: int, **context) -> None:
    log.info("load: %d summary rows committed for run %s", row_count, context["run_id"])


with DAG(
    dag_id="daily_summary",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["shadow-example", "minimal", "production"],
    doc_md=__doc__,
) as _:
    raw = extract()
    count = transform(raw)
    load(count)
