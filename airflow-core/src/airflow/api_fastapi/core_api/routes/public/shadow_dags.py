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
Shadow DAG REST API endpoints — AIP-09.

All endpoints require authentication via the standard Airflow JWT mechanism
and are included under the ``/api/v2/shadow-dags`` prefix.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import Depends, HTTPException, status

from airflow.api_fastapi.common.db.common import SessionDep
from airflow.api_fastapi.common.router import AirflowRouter
from airflow.api_fastapi.core_api.datamodels.shadow_dags import (
    ComparisonReportResponse,
    ShadowDagCollectionResponse,
    ShadowDagCreateBody,
    ShadowDagResponse,
)
from airflow.api_fastapi.core_api.openapi.exceptions import create_openapi_http_exception_doc
from airflow.models.shadow_dag import (
    InvalidShadowTransition,
    ShadowDagNotFound,
    ShadowDagStatus,
)
from airflow.shadow.lifecycle import ShadowDagService

shadow_dags_router = AirflowRouter(
    tags=["Shadow DAGs"],
    prefix="/shadow-dags",
    responses=create_openapi_http_exception_doc(
        [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND]
    ),
)

_service = ShadowDagService()


def _shadow_to_response(shadow) -> ShadowDagResponse:
    return ShadowDagResponse(
        shadow_id=shadow.shadow_id,
        production_dag_id=shadow.production_dag_id,
        candidate_dag_id=shadow.candidate_dag_id,
        status=shadow.status,
        ttl_days=shadow.ttl_days,
        divergence_alert_pct=shadow.divergence_alert_pct,
        notify=shadow.notify,
        created_at=shadow.created_at,
        expires_at=shadow.expires_at,
    )


@shadow_dags_router.get(
    "",
    summary="List Shadow DAG experiments",
)
def list_shadow_dags(
    session: SessionDep,
    status_filter: str | None = None,
    production_dag_id: str | None = None,
) -> ShadowDagCollectionResponse:
    """Return all Shadow DAG records, optionally filtered by status or production DAG."""
    status_enum: ShadowDagStatus | None = None
    if status_filter is not None:
        try:
            status_enum = ShadowDagStatus(status_filter)
        except ValueError:
            valid = [s.value for s in ShadowDagStatus]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status '{status_filter}'. Valid values: {valid}",
            )
    shadows = _service.list(
        status=status_enum,
        production_dag_id=production_dag_id,
        session=session,
    )
    return ShadowDagCollectionResponse(
        shadow_dags=[_shadow_to_response(s) for s in shadows],
        total_entries=len(shadows),
    )


@shadow_dags_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Shadow DAG experiment",
)
def create_shadow_dag(
    body: ShadowDagCreateBody,
    session: SessionDep,
) -> ShadowDagResponse:
    """Register a new Shadow DAG experiment against a production DAG."""
    try:
        shadow = _service.create(
            production_dag_id=body.production_dag_id,
            candidate_dag_id=body.candidate_dag_id,
            ttl=body.ttl,
            divergence_alert=body.divergence_alert,
            notify=body.notify,
            session=session,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    session.flush()
    return _shadow_to_response(shadow)


@shadow_dags_router.get(
    "/{shadow_id}",
    summary="Get a Shadow DAG record",
)
def get_shadow_dag(
    shadow_id: str,
    session: SessionDep,
) -> ShadowDagResponse:
    """Fetch a single Shadow DAG experiment by ID."""
    try:
        shadow = _service.get(shadow_id, session=session)
    except ShadowDagNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return _shadow_to_response(shadow)


@shadow_dags_router.delete(
    "/{shadow_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Discard a Shadow DAG experiment",
)
def discard_shadow_dag(
    shadow_id: str,
    session: SessionDep,
) -> None:
    """Discard a Shadow DAG and schedule it for cleanup."""
    try:
        _service.discard(shadow_id, session=session)
    except ShadowDagNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidShadowTransition as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@shadow_dags_router.post(
    "/{shadow_id}/promote",
    summary="Promote a Shadow DAG to production-ready status",
)
def promote_shadow_dag(
    shadow_id: str,
    session: SessionDep,
) -> ShadowDagResponse:
    """Transition a Shadow DAG in REVIEW state to PROMOTED."""
    try:
        shadow = _service.promote(shadow_id, session=session)
    except ShadowDagNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidShadowTransition as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _shadow_to_response(shadow)


@shadow_dags_router.get(
    "/{shadow_id}/reports/latest",
    summary="Get the latest comparison report for a Shadow DAG",
)
def get_latest_report(
    shadow_id: str,
    session: SessionDep,
) -> ComparisonReportResponse:
    """Return the most recent shadow vs. production comparison report."""
    try:
        shadow = _service.get(shadow_id, session=session)
    except ShadowDagNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if not shadow.last_comparison_json:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No comparison report available yet for shadow '{shadow_id}'.",
        )
    try:
        data = json.loads(shadow.last_comparison_json)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Comparison report data is corrupted: {exc}",
        )
    return ComparisonReportResponse(**data)
