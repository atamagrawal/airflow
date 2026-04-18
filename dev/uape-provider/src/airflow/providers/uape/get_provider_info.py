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


def get_provider_info() -> dict:
    """
    Return minimal provider metadata for the UAPE advisory distribution.

    CLI is registered here (``apache_airflow_provider``). The ``AirflowPlugin`` is loaded via the
    ``airflow.plugins`` entry point in ``pyproject.toml`` so it works when ``lazy_discover_providers``
    is left at the default ``True`` (provider-only ``plugins`` metadata would not load then).
    """
    return {
        "package-name": "apache-airflow-providers-uape",
        "name": "Dev UAPE advisory",
        "description": (
            "Read-only UAPE parallelization advisory from serialized DAGs: versioned JSON reports "
            "(metrics, confidence, limits), CLI export, HTTP API, and optional UI iframe. Does not execute user task code."
        ),
        "cli": ["airflow.providers.uape.cli.definition.get_uape_cli_commands"],
    }
