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

ARG_DAG_ID = Arg(("dag_id",), help="The id of the dag")

ARG_UAPE_FORMAT = Arg(
    ("--format",),
    help="Output format",
    choices=["text", "json"],
    default="text",
    type=str.lower,
)

ARG_EXTRA_CLEAR_TYPES = Arg(
    ("--extra-clear-types",),
    help=(
        "Comma-separated list of additional operator type names to treat as 'clear' (T3 tier, "
        "confidence=medium) for this invocation. Supplements the built-in T1/T2 allowlists and "
        "the UAPE_EXTRA_CLEAR_OPERATOR_TYPES environment variable. "
        "Example: --extra-clear-types MyCustomOperator,AnotherOperator"
    ),
    default="",
    type=str,
)


UAPE_COMMANDS = (
    ActionCommand(
        name="independence-report",
        help=(
            "List structurally independent task pairs; overlap hints for tasks on the clear allowlist "
            "(T1: trivial, T2: computation, T3: user-extended)"
        ),
        func=lazy_load_command("airflow.providers.uape.cli.commands.uape_independence_report"),
        args=(ARG_DAG_ID, ARG_UAPE_FORMAT, ARG_EXTRA_CLEAR_TYPES),
    ),
    ActionCommand(
        name="export",
        help="Export full JSON report (classifications, proofs, hints, abstentions)",
        func=lazy_load_command("airflow.providers.uape.cli.commands.uape_export"),
        args=(
            ARG_DAG_ID,
            Arg(("--format",), help="Only json is supported", choices=["json"], default="json"),
            ARG_EXTRA_CLEAR_TYPES,
        ),
    ),
)


def get_uape_cli_commands():
    """Return top-level CLI group definitions for UAPE advisory commands."""
    return [
        GroupCommand(
            name="uape",
            help="Advisory DAG parallelization hints (UAPE dev provider; read-only, serialized DAG)",
            subcommands=UAPE_COMMANDS,
        ),
    ]
