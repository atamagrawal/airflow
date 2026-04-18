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
from airflow.providers.uape.parallelization import (
    UAPE_EXTRA_CLEAR_TYPES_ENV,
    _parse_extra_clear_types,
    analyze_serialized_dag,
)
from airflow.utils import cli as cli_utils
from airflow.utils.providers_configuration_loader import providers_configuration_loaded
from airflow.utils.session import create_session


def _load_report(dag_id: str, extra_clear_types: frozenset[str] | None = None) -> dict[str, Any]:
    with create_session() as session:
        row = SerializedDagModel.get(dag_id, session=session)
    if row is None:
        raise AirflowException(
            f"No serialized DAG found for {dag_id!r}. Ensure the DAG is parsed and serialization is enabled."
        )
    dag = row.dag
    return analyze_serialized_dag(dag, extra_clear_types=extra_clear_types)


def _extra_clear_types_from_args(args) -> frozenset[str] | None:
    """
    Parse the ``--extra-clear-types`` CLI argument.

    Returns ``None`` when the argument is absent or empty so that
    ``analyze_serialized_dag`` falls back to the environment variable.
    """
    raw = getattr(args, "extra_clear_types", "") or ""
    if not raw.strip():
        return None
    return _parse_extra_clear_types(raw)


def _print_text_report(report: dict[str, Any]) -> None:
    print(f"DAG: {report['dag_id']} ({report['policy']})")
    schema = report.get("report_schema_version", "?")
    gen = report.get("generated_at_utc", "?")
    ver = report.get("uape_provider_version", "?")
    print(f"Report schema: {schema} · generated {gen} · provider {ver}")

    limits = report.get("analysis_limits") or {}
    if limits.get("full_independent_pair_analysis") is False:
        print(
            f"\nPair analysis skipped: {limits.get('reason', 'unknown')} "
            f"({limits.get('task_count', '?')} tasks > limit {limits.get('task_limit', '?')})."
        )

    metrics = report.get("graph_metrics") or {}
    if metrics:
        tier_counts = metrics.get("clear_tier_counts") or {}
        tier_str = ""
        if tier_counts:
            parts = [f"{k}={v}" for k, v in tier_counts.items() if v]
            tier_str = f" ({', '.join(parts)})" if parts else ""
        print(
            "\nGraph metrics: "
            f"{metrics.get('task_count', '?')} tasks, "
            f"{metrics.get('dependency_edge_count', '?')} edges, "
            f"{metrics.get('clear_task_count', '?')} clear{tier_str} / "
            f"{metrics.get('opaque_task_count', '?')} opaque (by allowlist)."
        )

    tiers = report.get("clear_operator_allowlist_tiers") or {}
    t3 = tiers.get("t3_user_extended") or []
    allowlist_str = ", ".join(report.get("clear_operator_allowlist") or []) or "(empty)"
    print(f"Clear operator allowlist: {allowlist_str}")
    if t3:
        print(f"  ↳ T3 user-extended (this run): {', '.join(sorted(t3))}")
    else:
        env_hint = UAPE_EXTRA_CLEAR_TYPES_ENV
        print(f"  ↳ extend with --extra-clear-types or ${env_hint}")

    summary = report.get("executive_summary")
    if summary:
        print(f"\n{summary}")

    hints = report["clear_task_overlap_hints"]
    if not hints:
        print(
            "\nNo parallel overlap opportunities (no clear–clear independent pairs in this serialized graph)."
        )
    else:
        print("\nAdvisory overlap hints (clear tasks only, declared graph):")
        for h in hints:
            conf = h.get("confidence", "")
            tier_a = h.get("task_a_clear_tier", "")
            tier_b = h.get("task_b_clear_tier", "")
            tier_str = f"  tiers: {tier_a}/{tier_b}" if tier_a or tier_b else ""
            suffix = f" [{conf}{tier_str}]" if conf else ""
            print(f"  - {h['task_a']!r} <~> {h['task_b']!r}{suffix}: {h['proof']['detail']}")

    abst = report["abstained_parallel_hints"]
    abst_total = report.get("abstained_parallel_hints_total", len(abst))
    if abst:
        cap_note = ""
        if abst_total > len(abst):
            cap_note = f" (showing {len(abst)} of {abst_total}; see JSON for cap metadata)"
        print(
            f"\nOpaque-involved independent pairs (reference only — not parallel recommendations; "
            f"{abst_total} pair(s)){cap_note}:"
        )
        for a in abst[:50]:
            print(f"  - {a['task_a']!r} <~> {a['task_b']!r}: {a['abstain_reason']}")
        if len(abst) > 50:
            print(f"  ... and {len(abst) - 50} more in this slice (use `airflow uape export` for full JSON)")


@cli_utils.action_cli
@providers_configuration_loaded
def uape_independence_report(args) -> None:
    extra = _extra_clear_types_from_args(args)
    report = _load_report(args.dag_id, extra_clear_types=extra)
    if args.format == "json":
        json.dump(_independence_subset(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    _print_text_report(report)


def _independence_subset(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "dag_id",
        "policy",
        "report_schema_version",
        "generated_at_utc",
        "uape_provider_version",
        "graph_metrics",
        "analysis_limits",
        "executive_summary",
        "clear_operator_allowlist",
        "clear_operator_allowlist_tiers",
        "clear_task_overlap_hints",
        "clear_task_overlap_hints_count",
        "abstained_parallel_hints",
        "abstained_parallel_hints_total",
        "abstained_parallel_hints_returned",
    )
    return {key: report[key] for key in keys if key in report}


@cli_utils.action_cli
@providers_configuration_loaded
def uape_export(args) -> None:
    extra = _extra_clear_types_from_args(args)
    report = _load_report(args.dag_id, extra_clear_types=extra)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
