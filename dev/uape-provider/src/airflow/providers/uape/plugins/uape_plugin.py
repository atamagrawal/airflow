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
Airflow plugin: mounts the UAPE read-only API and UI tabs (iframe) on DAG-related screens.

``url_route`` values must be unique across all plugins (Airflow deduplicates globally).

When the UI groups plugin views by ``category``, the parent tab is the generic **Recommendations**
panel. Each view's ``name`` should describe *this* recommendation stream (for sub-navigation when
several providers share the category), not rename the panel.
"""

from __future__ import annotations

from airflow.plugins_manager import AirflowPlugin

_UAPE_IFRAME_HREF = "/uape/dags/{DAG_ID}/recommendations-ui"


def _uape_fastapi_metadata() -> dict:
    from airflow.providers.uape.web.app import create_uape_app

    return {
        "app": create_uape_app(),
        "url_prefix": "/uape",
        "name": "UAPE advisory API",
    }


class UapeAdvisoryPlugin(AirflowPlugin):
    """Registers FastAPI routes under ``/uape`` and plugin tabs wherever the DAG context exists in the UI."""

    name = "uape_advisory"
    fastapi_apps = [_uape_fastapi_metadata()]
    external_views = [
        {
            "name": "Recommendations",
            "category": "recommendations",
            "destination": "dag",
            "url_route": "uape-recommendations",
            "href": _UAPE_IFRAME_HREF,
        },
        {
            "name": "Recommendations",
            "category": "recommendations",
            "destination": "dag_run",
            "url_route": "uape-recommendations-run",
            "href": _UAPE_IFRAME_HREF,
        },
        {
            "name": "Recommendations",
            "category": "recommendations",
            "destination": "task",
            "url_route": "uape-recommendations-task",
            "href": _UAPE_IFRAME_HREF,
        },
        {
            "name": "Recommendations",
            "category": "recommendations",
            "destination": "task_instance",
            "url_route": "uape-recommendations-ti",
            "href": _UAPE_IFRAME_HREF,
        },
    ]
