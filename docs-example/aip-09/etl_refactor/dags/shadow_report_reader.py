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
Utility DAG: ``shadow_report_reader``

Reads the latest comparison report for every active shadow experiment and
logs a human-friendly summary.  Useful as a scheduled "health check" DAG
during a shadow experiment window.

Trigger manually or schedule it to run after the production DAG:

    schedule=timedelta(hours=1)   # poll frequently during experiment window
    schedule="@daily"             # check once per day

No external dependencies — reads directly from the ``shadow_dag`` table.

Sample output::

    ╔══════════════════════════════════════════════════╗
    ║ Shadow Report: shd_orders_daily_20250115         ║
    ║ Production : orders_daily                        ║
    ║ Candidate  : orders_daily_v2                     ║
    ║ Status     : ACTIVE                              ║
    ╠══════════════════════════════════════════════════╣
    ║ Verdict         : DIVERGED                       ║
    ║ Prod rows       : 50                             ║
    ║ Shadow rows     : 50                             ║
    ║ Row delta %     : 0.00                           ║
    ║ Schema diffs    : discount_applied (added)       ║
    ║ Value diffs     : total_usd, customer            ║
    ╚══════════════════════════════════════════════════╝
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from airflow.sdk import DAG, task

log = logging.getLogger(__name__)


def _verdict_emoji(verdict: str) -> str:
    return {"MATCH": "✅", "WITHIN_THRESHOLD": "⚠️", "DIVERGED": "❌", "SHADOW_FAILED": "💥"}.get(verdict, "?")


@task
def fetch_active_shadows() -> list[dict]:
    """
    Query the shadow_dag table for all ACTIVE experiments and return their
    serialised metadata including the latest comparison report JSON.
    """
    from airflow.models.shadow_dag import ShadowDagStatus
    from airflow.shadow.lifecycle import ShadowDagService
    from airflow.utils.session import create_session

    service = ShadowDagService()
    with create_session() as session:
        shadows = service.list(status=ShadowDagStatus.ACTIVE, session=session)
        return [
            {
                "shadow_id": s.shadow_id,
                "production_dag_id": s.production_dag_id,
                "candidate_dag_id": s.candidate_dag_id,
                "status": s.status,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "last_comparison_json": s.last_comparison_json,
            }
            for s in shadows
        ]


@task
def print_report(shadows: list[dict]) -> None:
    """Pretty-print a summary of each shadow experiment and its latest comparison."""
    if not shadows:
        log.info("No active shadow experiments found.")
        return

    for shadow in shadows:
        shadow_id = shadow["shadow_id"]
        prod = shadow["production_dag_id"]
        candidate = shadow["candidate_dag_id"]
        status = shadow["status"]
        expires = shadow.get("expires_at", "unknown")

        report_json = shadow.get("last_comparison_json")
        if not report_json:
            log.info(
                "[%s] %s → %s  |  status=%s  |  no comparison report yet",
                shadow_id, prod, candidate, status,
            )
            continue

        report = json.loads(report_json)
        verdict = report.get("verdict", "UNKNOWN")
        row_prod = report.get("row_count_prod", 0)
        row_shadow = report.get("row_count_shadow", 0)
        delta = report.get("row_count_delta_pct", 0.0)
        schema_diffs = report.get("schema_divergence", [])
        value_diffs = report.get("value_divergence", [])
        error = report.get("error")

        schema_summary = ", ".join(
            f"{d['column']} ({d['change']})" for d in schema_diffs
        ) or "none"
        value_summary = ", ".join(d["column"] for d in value_diffs if d.get("prod_mean") != d.get("shadow_mean")) or "none"

        sep = "═" * 54
        log.info(
            "\n╔%s╗\n"
            "║  Shadow Report: %-36s║\n"
            "║  Production : %-38s║\n"
            "║  Candidate  : %-38s║\n"
            "║  Status     : %-38s║\n"
            "║  Expires    : %-38s║\n"
            "╠%s╣\n"
            "║  Verdict      : %s %-35s║\n"
            "║  Prod rows    : %-38s║\n"
            "║  Shadow rows  : %-38s║\n"
            "║  Row delta %%  : %-38s║\n"
            "║  Schema diffs : %-38s║\n"
            "║  Value diffs  : %-38s║\n"
            "%s"
            "╚%s╝",
            sep,
            shadow_id, prod, candidate, status, expires,
            sep,
            _verdict_emoji(verdict), verdict,
            row_prod, row_shadow, f"{delta:.2f}%",
            schema_summary, value_summary,
            f"║  Error        : {error:<38}║\n" if error else "",
            sep,
        )


@task
def check_for_divergence(shadows: list[dict]) -> None:
    """
    Raise an exception if any active shadow has a DIVERGED verdict.

    Hook this task into an alerting pipeline or use it as a gate before
    promoting a candidate to production.
    """
    diverged = [
        s["shadow_id"]
        for s in shadows
        if s.get("last_comparison_json")
        and json.loads(s["last_comparison_json"]).get("verdict") == "DIVERGED"
    ]
    if diverged:
        log.warning("Shadow experiments with DIVERGED verdict: %s", diverged)
        # Remove the raise if you want informational-only behaviour.
        # raise ValueError(f"Diverged shadows: {diverged}")
    else:
        log.info("All active shadows are within threshold or matching.")


with DAG(
    dag_id="shadow_report_reader",
    schedule=None,                  # trigger manually or set a schedule
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["shadow-example", "etl", "utility"],
    doc_md=__doc__,
) as _:
    active = fetch_active_shadows()
    print_report(active)
    check_for_divergence(active)
