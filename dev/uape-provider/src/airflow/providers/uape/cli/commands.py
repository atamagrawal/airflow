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

from __future__ import annotations

import json
import sys
from typing import Any

from airflow.exceptions import AirflowException
from airflow.models.serialized_dag import SerializedDagModel
from airflow.providers.uape.parallelization import N_SIMULATIONS, analyze_dag_edges
from airflow.utils import cli as cli_utils
from airflow.utils.providers_configuration_loader import providers_configuration_loaded
from airflow.utils.session import create_session

_VERDICT_COLOR = {
    "remove": "\033[33m",  # yellow
    "uncertain": "\033[36m",  # cyan
    "keep": "\033[32m",  # green
}
_RESET = "\033[0m"


def _load_report(dag_id: str, *, simulate: bool = True) -> dict[str, Any]:
    n = N_SIMULATIONS if simulate else 0
    with create_session() as session:
        row = SerializedDagModel.get(dag_id, session=session)
        if row is None:
            raise AirflowException(
                f"No serialized DAG found for {dag_id!r}. "
                f"Ensure the DAG is parsed and serialization is enabled."
            )
        return analyze_dag_edges(row.dag, session=session, n_simulations=n)


def _verdict_label(verdict: str) -> str:
    color = _VERDICT_COLOR.get(verdict, "")
    label = verdict.upper().ljust(9)
    return f"{color}{label}{_RESET}" if sys.stdout.isatty() else label


def _fmt_savings(ts: dict[str, Any]) -> str:
    p50 = ts.get("p50_savings_seconds", 0)
    p5 = ts.get("p5_savings_seconds", 0)
    p95 = ts.get("p95_savings_seconds", 0)
    prob = ts.get("prob_improvement", 0)
    return (
        f"{p50 / 60:.1f} min median  "
        f"({p5 / 60:.1f}–{p95 / 60:.1f} min range)  "
        f"{prob * 100:.0f}% probability of improvement"
    )


def _print_text_report(report: dict[str, Any], verdict_filter: str = "all") -> None:
    dag_id = report["dag_id"]
    schema = report.get("report_schema_version", "?")
    gen = report.get("generated_at_utc", "?")
    ver = report.get("uape_provider_version", "?")
    policy = report.get("policy", "?")

    print(f"DAG: {dag_id}  (policy: {policy})")
    print(f"Schema: {schema} · generated {gen} · provider {ver}")

    gm = report.get("graph_metrics") or {}
    print(
        f"\nGraph: {gm.get('task_count', '?')} tasks, "
        f"{gm.get('dependency_edge_count', '?')} declared edges, "
        f"{gm.get('redundant_edge_count', 0)} redundant (transitive)"
    )

    summary = report.get("summary") or {}
    has_data = summary.get("has_historical_data", False)
    profiled = summary.get("profiled_task_count", 0)
    data_note = f"  [{profiled} tasks profiled from history]" if has_data else "  [no historical data]"
    print(
        f"Summary: {summary.get('remove_count', 0)} remove, "
        f"{summary.get('uncertain_count', 0)} uncertain, "
        f"{summary.get('keep_count', 0)} keep" + data_note
    )

    redundant = report.get("redundant_edges") or []
    if redundant:
        print("\nRedundant edges (already covered by longer paths — remove safely):")
        for r in redundant:
            print(f"  {r['from_task']} >> {r['to_task']}")

    edges = report.get("edge_analyses") or []
    if verdict_filter != "all":
        edges = [e for e in edges if e["verdict"] == verdict_filter]

    if not edges:
        print("\nNo edge analyses to display.")
        return

    print(f"\nEdge analyses ({len(edges)} shown):\n")
    for edge in edges:
        from_t = edge["from_task"]
        to_t = edge["to_task"]
        verdict = edge["verdict"]
        score = edge["confidence_score"]
        label = _verdict_label(verdict)

        print(f"  {label}  {from_t} >> {to_t}   score={score}/100")

        for sig in edge.get("signals", []):
            skipped = sig.get("skipped", False)
            status = "skip" if skipped else ("✓" if sig["passed"] else "✗")
            if sys.stdout.isatty():
                color = "\033[90m" if skipped else ("\033[32m" if sig["passed"] else "\033[31m")
                status_str = f"{color}{status}{_RESET}"
            else:
                status_str = status
            print(f"    [{status_str}] {sig['name']:22s} {sig['explanation']}")

        if edge.get("suggested_fix"):
            print(f"    → Suggestion: {edge['suggested_fix']}")

        ts = edge.get("time_savings")
        if ts:
            print(f"    ⏱  Est. saving: {_fmt_savings(ts)}")

        print()

    print(report.get("executive_summary", ""))


@cli_utils.action_cli
@providers_configuration_loaded
def uape_analyze(args) -> None:
    simulate = not getattr(args, "no_simulate", False)
    report = _load_report(args.dag_id, simulate=simulate)

    if args.format == "json":
        verdict_filter = getattr(args, "verdict", "all")
        if verdict_filter != "all":
            report = dict(report)
            report["edge_analyses"] = [e for e in report["edge_analyses"] if e["verdict"] == verdict_filter]
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return

    _print_text_report(report, verdict_filter=getattr(args, "verdict", "all"))


@cli_utils.action_cli
@providers_configuration_loaded
def uape_export(args) -> None:
    simulate = not getattr(args, "no_simulate", False)
    report = _load_report(args.dag_id, simulate=simulate)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
