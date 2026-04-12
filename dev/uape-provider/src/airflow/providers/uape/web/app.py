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

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from airflow.exceptions import AirflowException
from airflow.models.serialized_dag import SerializedDagModel
from airflow.providers.uape.parallelization import analyze_serialized_dag
from airflow.utils.session import create_session


def _load_report_or_raise(dag_id: str) -> dict[str, Any]:
    with create_session() as session:
        row = SerializedDagModel.get(dag_id, session=session)
    if row is None:
        raise AirflowException(
            f"No serialized DAG found for {dag_id!r}. Ensure the DAG is parsed and serialization is enabled."
        )
    return analyze_serialized_dag(row.dag)


def create_uape_app() -> FastAPI:
    """FastAPI sub-application mounted under ``/uape`` on the API server (JSON only)."""
    app = FastAPI(
        title="UAPE advisory API",
        description="Read-only parallelization hints for third-party clients. Not shown in the Airflow UI.",
        version="0.0.1",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/dags/{dag_id}/recommendations.json")
    def recommendations_json(dag_id: str) -> JSONResponse:
        try:
            report = _load_report_or_raise(dag_id)
        except AirflowException as e:
            return JSONResponse(status_code=404, content={"error": str(e)})
        return JSONResponse(report)

    return app
