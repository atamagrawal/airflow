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
from airflow.providers.uape.parallelization import analyze_serialized_dag
from airflow.utils import cli as cli_utils
from airflow.utils.providers_configuration_loader import providers_configuration_loaded
from airflow.utils.session import create_session


def _load_report(dag_id: str) -> dict[str, Any]:
    with create_session() as session:
        row = SerializedDagModel.get(dag_id, session=session)
    if row is None:
        raise AirflowException(
            f"No serialized DAG found for {dag_id!r}. Ensure the DAG is parsed and serialization is enabled."
        )
    dag = row.dag
    return analyze_serialized_dag(dag)


def _print_text_report(report: dict[str, Any]) -> None:
    print(f"DAG: {report['dag_id']} ({report['policy']})")
    print(f"Clear operator allowlist: {', '.join(report['clear_operator_allowlist']) or '(empty)'}")
    hints = report["clear_task_overlap_hints"]
    if not hints:
        print("\nNo clear-task overlap hints (all pairs abstained or no structurally independent pairs).")
    else:
        print("\nAdvisory overlap hints (clear tasks only, declared graph):")
        for h in hints:
            print(f"  - {h['task_a']!r} <~> {h['task_b']!r}: {h['proof']['detail']}")
    abst = report["abstained_parallel_hints"]
    if abst:
        print(f"\nAbstentions ({len(abst)} structurally independent pair(s) involving opaque tasks):")
        for a in abst[:50]:
            print(f"  - {a['task_a']!r} <~> {a['task_b']!r}: {a['abstain_reason']}")
        if len(abst) > 50:
            print(f"  ... and {len(abst) - 50} more (use `airflow uape export` for full JSON)")


@cli_utils.action_cli
@providers_configuration_loaded
def uape_independence_report(args) -> None:
    report = _load_report(args.dag_id)
    if args.format == "json":
        json.dump(_independence_subset(report), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    _print_text_report(report)


def _independence_subset(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "dag_id": report["dag_id"],
        "policy": report["policy"],
        "clear_operator_allowlist": report["clear_operator_allowlist"],
        "clear_task_overlap_hints": report["clear_task_overlap_hints"],
        "abstained_parallel_hints": report["abstained_parallel_hints"],
    }


@cli_utils.action_cli
@providers_configuration_loaded
def uape_export(args) -> None:
    report = _load_report(args.dag_id)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
