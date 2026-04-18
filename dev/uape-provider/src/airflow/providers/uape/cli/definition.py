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

from airflow.cli.cli_config import ActionCommand, Arg, GroupCommand, lazy_load_command

ARG_DAG_ID = Arg(("dag_id",), help="The DAG id to analyse")

ARG_FORMAT = Arg(
    ("--format",),
    help="Output format",
    choices=["text", "json"],
    default="text",
    type=str.lower,
)

ARG_VERDICT = Arg(
    ("--verdict",),
    help="Filter output to edges with this verdict only",
    choices=["remove", "uncertain", "keep", "all"],
    default="all",
    type=str.lower,
)

ARG_NO_SIMULATE = Arg(
    ("--no-simulate",),
    help="Skip Monte Carlo time-savings simulation (faster, no historical data needed)",
    action="store_true",
    default=False,
)

UAPE_COMMANDS = (
    ActionCommand(
        name="analyze",
        help=(
            "Analyse declared DAG edges for false dependencies using four signals "
            "(asset overlap, XCom analysis, timing correlation, transitive reduction). "
            "Scores each edge and recommends whether to keep, review, or remove it."
        ),
        func=lazy_load_command("airflow.providers.uape.cli.commands.uape_analyze"),
        args=(ARG_DAG_ID, ARG_FORMAT, ARG_VERDICT, ARG_NO_SIMULATE),
    ),
    ActionCommand(
        name="export",
        help="Export full JSON analysis report for a DAG (all edges, signals, simulation results)",
        func=lazy_load_command("airflow.providers.uape.cli.commands.uape_export"),
        args=(
            ARG_DAG_ID,
            Arg(("--format",), help="Only json is supported", choices=["json"], default="json"),
            ARG_NO_SIMULATE,
        ),
    ),
)


def get_uape_cli_commands():
    """Return top-level CLI group definitions for UAPE advisory commands."""
    return [
        GroupCommand(
            name="uape",
            help=(
                "Uncertainty-Aware Parallelization Engine: analyse DAG edges for false dependencies "
                "and estimate time savings (read-only, uses serialized DAG + historical TaskInstance data)"
            ),
            subcommands=UAPE_COMMANDS,
        ),
    ]
