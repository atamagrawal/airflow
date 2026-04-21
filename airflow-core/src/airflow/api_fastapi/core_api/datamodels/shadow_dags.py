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
"""Pydantic datamodels for the Shadow DAG public API — AIP-09."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from airflow.api_fastapi.core_api.base import BaseModel, StrictBaseModel


class ShadowDagCreateBody(StrictBaseModel):
    """Request body for creating a Shadow DAG experiment."""

    production_dag_id: str
    candidate_dag_id: str
    ttl: str = Field(default="7d", description="TTL string, e.g. '7d'. Maximum 14 days.")
    divergence_alert: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Row-count divergence fraction at which to alert (0.05 = 5%).",
    )
    notify: str | None = None


class ShadowDagResponse(BaseModel):
    """Serialised Shadow DAG experiment record."""

    shadow_id: str
    production_dag_id: str
    candidate_dag_id: str
    status: str
    ttl_days: int
    divergence_alert_pct: float
    notify: str | None
    created_at: datetime
    expires_at: datetime


class ShadowDagCollectionResponse(BaseModel):
    """Collection of Shadow DAG records."""

    shadow_dags: list[ShadowDagResponse]
    total_entries: int


class ColumnDiffResponse(BaseModel):
    column: str
    change: str
    prod_type: str | None = None
    shadow_type: str | None = None


class FieldStatsResponse(BaseModel):
    column: str
    prod_null_rate: float = 0.0
    shadow_null_rate: float = 0.0
    prod_min: Any = None
    shadow_min: Any = None
    prod_max: Any = None
    shadow_max: Any = None
    prod_mean: float | None = None
    shadow_mean: float | None = None


class ComparisonReportResponse(BaseModel):
    """Shadow vs. production comparison report (AIP-09 §7)."""

    run_id: str
    shadow_id: str
    row_count_prod: int
    row_count_shadow: int
    row_count_delta_pct: float
    schema_divergence: list[ColumnDiffResponse] = []
    value_divergence: list[FieldStatsResponse] = []
    sample_diff_rows: list[dict[str, Any]] = []
    verdict: str
    error: str | None = None
