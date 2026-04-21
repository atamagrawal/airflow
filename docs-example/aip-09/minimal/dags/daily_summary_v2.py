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
Shadow candidate DAG: ``daily_summary_v2``

This is the **candidate** for ``daily_summary``.  It uses ``@shadow_dag`` so
that:

* The DAG Processor auto-registers a ``ShadowDag`` record on first parse.
* The scheduler spawns a shadow ``DagRun`` after every production run of
  ``daily_summary``.
* After each shadow run the ``ComparisonEngine`` diffs the two JSONL outputs
  and stores a ``ComparisonReport`` on the ``ShadowDag`` record.

Key differences from v1 (intentional divergence for demo purposes):

* ``transform_v2`` adds a ``transaction_count`` field per user.
* For user ``u3`` the total is capped at 50.0 to simulate a business rule
  change — this will surface as a value divergence in the comparison report.

Usage
-----
1. Drop both DAG files into your DAGs folder.
2. On next DAG parse the shadow experiment is auto-registered.
3. Trigger ``daily_summary`` manually; the scheduler creates a parallel shadow
   run for ``daily_summary_v2``.
4. Check the report::

       airflow shadow list
       airflow shadow report --shadow-id <shadow_id>

The Shadow Reports tab in the Airflow UI shows the same data visually.
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from airflow.sdk import DAG, task
from airflow.sdk.definitions.shadow import shadow_dag

log = logging.getLogger(__name__)

_PROD_OUTPUT_DIR = Path(os.environ.get("AIRFLOW_HOME", "~/airflow")).expanduser() / "prod_output" / "daily_summary"


def _raw_events(logical_date: datetime) -> list[dict]:
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
def transform_v2(raw_events: list[dict], **context) -> int:
    """
    Candidate algorithm v2:
    - Groups by user_id, sums amount, and adds transaction_count.
    - Applies a cap of 50.0 on total_amount for user ``u3`` (new business rule).

    Output is written to the shadow sink path injected by ``LocalFileSinkProxy``
    if running as a shadow task, otherwise to the same prod output directory.
    This dual-write pattern lets you test the candidate locally even before
    the shadow scheduler integration kicks in.
    """
    run_id: str = context["run_id"]

    # LocalFileSinkProxy injects this key into the task context.
    shadow_output_path: str | None = context.get("shadow_output_path")

    totals: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for evt in raw_events:
        totals[evt["user_id"]] += evt["amount"]
        counts[evt["user_id"]] += 1

    rows = []
    for uid in sorted(totals):
        total = round(totals[uid], 2)
        # New business rule: cap u3 at 50
        if uid == "u3" and total > 50.0:
            total = 50.0
        rows.append({"user_id": uid, "total_amount": total, "transaction_count": counts[uid]})

    if shadow_output_path:
        output_path = Path(shadow_output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        fallback_dir = _PROD_OUTPUT_DIR / run_id / "transform_v2"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        output_path = fallback_dir / "output.jsonl"

    with output_path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    log.info(
        "transform_v2: wrote %d summary rows to %s (shadow=%s)",
        len(rows),
        output_path,
        shadow_output_path is not None,
    )
    return len(rows)


@task
def load(row_count: int, **context) -> None:
    log.info("load: %d summary rows committed for run %s", row_count, context["run_id"])


@shadow_dag(
    shadows="daily_summary",
    ttl="7d",
    divergence_alert=0.05,   # alert when row count diverges by more than 5 %
    notify=None,             # set to "you@example.com" to receive e-mail alerts
)
@DAG(
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["shadow-example", "minimal", "shadow"],
    doc_md=__doc__,
)
def daily_summary_v2():
    raw = extract()
    count = transform_v2(raw)
    load(count)


daily_summary_v2()
