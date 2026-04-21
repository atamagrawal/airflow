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
"""Shadow DAG CLI commands — AIP-09 §6."""

from __future__ import annotations

import json
import logging
import os

from airflow.cli.simple_table import AirflowConsole
from airflow.utils import cli as cli_utils
from airflow.utils.session import create_session

log = logging.getLogger(__name__)


def _require_shadow_id(args) -> str:
    if not args.shadow_id:
        raise SystemExit("--shadow-id is required for this command.")
    return args.shadow_id


@cli_utils.action_cli
def shadow_create(args) -> None:
    """Register a new Shadow DAG experiment."""
    from airflow.shadow.lifecycle import ShadowDagService

    candidate_file = getattr(args, "candidate_file", None)
    if candidate_file and not os.path.isfile(candidate_file):
        raise SystemExit(f"Candidate file not found: {candidate_file}")

    candidate_dag_id = getattr(args, "candidate_dag_id", None) or args.production_dag
    ttl = getattr(args, "ttl", "7d") or "7d"
    divergence_alert = getattr(args, "divergence_alert", 0.05)
    notify = getattr(args, "notify", None)

    service = ShadowDagService()
    with create_session() as session:
        shadow = service.create(
            production_dag_id=args.production_dag,
            candidate_dag_id=candidate_dag_id,
            ttl=ttl,
            divergence_alert=float(divergence_alert),
            notify=notify,
            session=session,
        )
        session.flush()
        shadow_id = shadow.shadow_id

    console = AirflowConsole()
    console.print(f"Shadow DAG registered: [bold]{shadow_id}[/bold]")
    console.print(f"  Production DAG : {args.production_dag}")
    console.print(f"  Candidate DAG  : {candidate_dag_id}")
    console.print(f"  TTL            : {ttl}")
    console.print(f"  Alert threshold: {float(divergence_alert) * 100:.1f}%")
    if notify:
        console.print(f"  Notify         : {notify}")


@cli_utils.action_cli
def shadow_list(args) -> None:
    """List Shadow DAG experiments."""
    from airflow.models.shadow_dag import ShadowDagStatus
    from airflow.shadow.lifecycle import ShadowDagService

    status_filter = None
    if getattr(args, "status", None):
        try:
            status_filter = ShadowDagStatus(args.status)
        except ValueError:
            valid = [s.value for s in ShadowDagStatus]
            raise SystemExit(f"Invalid status '{args.status}'. Valid values: {valid}")

    service = ShadowDagService()
    with create_session() as session:
        shadows = service.list(status=status_filter, session=session)

    console = AirflowConsole()
    if not shadows:
        console.print("No Shadow DAGs found.")
        return

    rows = [
        {
            "shadow_id": s.shadow_id,
            "production_dag_id": s.production_dag_id,
            "candidate_dag_id": s.candidate_dag_id,
            "status": s.status,
            "ttl_days": s.ttl_days,
            "expires_at": s.expires_at.strftime("%Y-%m-%d"),
        }
        for s in shadows
    ]
    console.print_as(
        data=rows,
        output=getattr(args, "output", "table"),
        mapper=lambda x: x,
    )


@cli_utils.action_cli
def shadow_report(args) -> None:
    """Show the latest comparison report for a Shadow DAG."""
    from airflow.shadow.lifecycle import ShadowDagService

    shadow_id = _require_shadow_id(args)
    service = ShadowDagService()
    with create_session() as session:
        shadow = service.get(shadow_id, session=session)
        report_json = shadow.last_comparison_json

    console = AirflowConsole()
    if not report_json:
        console.print(f"No comparison report available yet for shadow '{shadow_id}'.")
        return

    report = json.loads(report_json)
    console.print(f"[bold]Shadow Report — {shadow_id}[/bold]")
    console.print(f"  Run ID          : {report.get('run_id', 'N/A')}")
    console.print(f"  Verdict         : [bold]{report.get('verdict', 'N/A')}[/bold]")
    console.print(f"  Prod row count  : {report.get('row_count_prod', 0)}")
    console.print(f"  Shadow row count: {report.get('row_count_shadow', 0)}")
    console.print(f"  Row delta       : {report.get('row_count_delta_pct', 0):.2f}%")

    schema_diffs = report.get("schema_divergence", [])
    if schema_diffs:
        console.print(f"\n  Schema divergences ({len(schema_diffs)}):")
        for diff in schema_diffs:
            console.print(f"    - {diff['column']}: {diff['change']}")

    sample_diffs = report.get("sample_diff_rows", [])
    if sample_diffs:
        console.print(f"\n  Sample differing rows (up to 10 shown):")
        for row in sample_diffs[:10]:
            console.print(f"    prod  : {row.get('prod')}")
            console.print(f"    shadow: {row.get('shadow')}")

    if report.get("error"):
        console.print(f"\n  [red]Error: {report['error']}[/red]")


@cli_utils.action_cli
def shadow_promote(args) -> None:
    """Promote a Shadow DAG to production-ready status."""
    from airflow.shadow.lifecycle import ShadowDagService

    shadow_id = _require_shadow_id(args)
    service = ShadowDagService()
    with create_session() as session:
        shadow = service.promote(shadow_id, session=session)
        candidate_dag_id = shadow.candidate_dag_id

    console = AirflowConsole()
    console.print(f"[green]Shadow DAG '{shadow_id}' promoted.[/green]")
    console.print(
        f"Next steps: deploy '{candidate_dag_id}' as the new production DAG "
        f"and run 'airflow shadow discard --shadow-id {shadow_id}' to clean up."
    )


@cli_utils.action_cli
def shadow_discard(args) -> None:
    """Discard a Shadow DAG and queue it for cleanup."""
    from airflow.shadow.lifecycle import ShadowDagService

    shadow_id = _require_shadow_id(args)
    console = AirflowConsole()

    if not getattr(args, "yes", False):
        confirm = input(f"Discard shadow '{shadow_id}'? This cannot be undone. [y/N] ")
        if confirm.lower() not in ("y", "yes"):
            console.print("Aborted.")
            return

    service = ShadowDagService()
    with create_session() as session:
        service.discard(shadow_id, session=session)

    console.print(f"Shadow DAG '{shadow_id}' discarded. It will be cleaned up automatically.")
