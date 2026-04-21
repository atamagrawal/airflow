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
"""Tests for the Shadow DAGs REST API (AIP-09)."""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from airflow._shared.timezones import timezone
from airflow.models.shadow_dag import InvalidShadowTransition, ShadowDag, ShadowDagNotFound, ShadowDagStatus


def _make_shadow(status=ShadowDagStatus.REGISTERED, shadow_id="shd_prod_20260420"):
    now = timezone.utcnow()
    shadow = ShadowDag(
        shadow_id=shadow_id,
        production_dag_id="prod_dag",
        candidate_dag_id="prod_dag_v2",
        status=status.value,
        ttl_days=7,
        divergence_alert_pct=0.05,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )
    return shadow


class TestListShadowDags:
    def test_list_returns_empty(self, test_client):
        with patch("airflow.shadow.lifecycle.ShadowDagService.list", return_value=[]):
            resp = test_client.get("/shadow-dags")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_entries"] == 0
        assert body["shadow_dags"] == []

    def test_list_returns_records(self, test_client):
        shadows = [_make_shadow(), _make_shadow(shadow_id="shd_other_20260420")]
        with patch("airflow.shadow.lifecycle.ShadowDagService.list", return_value=shadows):
            resp = test_client.get("/shadow-dags")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_entries"] == 2

    def test_list_invalid_status_filter(self, test_client):
        resp = test_client.get("/shadow-dags?status_filter=not_a_status")
        assert resp.status_code == 400


class TestCreateShadowDag:
    def test_create_success(self, test_client):
        shadow = _make_shadow()
        with (
            patch("airflow.shadow.lifecycle.ShadowDagService.create", return_value=shadow),
            patch("airflow.shadow.lifecycle.Stats"),
        ):
            resp = test_client.post(
                "/shadow-dags",
                json={
                    "production_dag_id": "prod_dag",
                    "candidate_dag_id": "prod_dag_v2",
                    "ttl": "7d",
                    "divergence_alert": 0.05,
                },
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["shadow_id"] == shadow.shadow_id

    def test_create_invalid_ttl(self, test_client):
        with patch(
            "airflow.shadow.lifecycle.ShadowDagService.create",
            side_effect=ValueError("Invalid TTL '99d'"),
        ):
            resp = test_client.post(
                "/shadow-dags",
                json={
                    "production_dag_id": "prod_dag",
                    "candidate_dag_id": "v2",
                    "ttl": "99d",
                },
            )
        assert resp.status_code == 400


class TestGetShadowDag:
    def test_get_found(self, test_client):
        shadow = _make_shadow()
        with patch("airflow.shadow.lifecycle.ShadowDagService.get", return_value=shadow):
            resp = test_client.get(f"/shadow-dags/{shadow.shadow_id}")
        assert resp.status_code == 200
        assert resp.json()["shadow_id"] == shadow.shadow_id

    def test_get_not_found(self, test_client):
        with patch(
            "airflow.shadow.lifecycle.ShadowDagService.get",
            side_effect=ShadowDagNotFound("missing"),
        ):
            resp = test_client.get("/shadow-dags/missing")
        assert resp.status_code == 404


class TestDiscardShadowDag:
    def test_discard_success(self, test_client):
        shadow = _make_shadow(ShadowDagStatus.ACTIVE)
        shadow.status = ShadowDagStatus.DISCARDED.value
        with (
            patch("airflow.shadow.lifecycle.ShadowDagService.discard", return_value=shadow),
            patch("airflow.shadow.lifecycle.Stats"),
        ):
            resp = test_client.delete("/shadow-dags/shd_prod_20260420")
        assert resp.status_code == 204

    def test_discard_not_found(self, test_client):
        with patch(
            "airflow.shadow.lifecycle.ShadowDagService.discard",
            side_effect=ShadowDagNotFound("missing"),
        ):
            resp = test_client.delete("/shadow-dags/missing")
        assert resp.status_code == 404


class TestPromoteShadowDag:
    def test_promote_success(self, test_client):
        shadow = _make_shadow(ShadowDagStatus.REVIEW)
        shadow.status = ShadowDagStatus.PROMOTED.value
        with (
            patch("airflow.shadow.lifecycle.ShadowDagService.promote", return_value=shadow),
            patch("airflow.shadow.lifecycle.Stats"),
        ):
            resp = test_client.post("/shadow-dags/shd_prod_20260420/promote")
        assert resp.status_code == 200
        assert resp.json()["status"] == "promoted"

    def test_promote_invalid_transition(self, test_client):
        with patch(
            "airflow.shadow.lifecycle.ShadowDagService.promote",
            side_effect=InvalidShadowTransition(ShadowDagStatus.REGISTERED, ShadowDagStatus.PROMOTED),
        ):
            resp = test_client.post("/shadow-dags/shd_prod_20260420/promote")
        assert resp.status_code == 400


class TestGetLatestReport:
    def test_no_report_returns_404(self, test_client):
        shadow = _make_shadow()
        shadow.last_comparison_json = None
        with patch("airflow.shadow.lifecycle.ShadowDagService.get", return_value=shadow):
            resp = test_client.get("/shadow-dags/shd_prod_20260420/reports/latest")
        assert resp.status_code == 404

    def test_report_returned(self, test_client):
        shadow = _make_shadow()
        shadow.last_comparison_json = json.dumps(
            {
                "run_id": "run_1",
                "shadow_id": "shd_prod_20260420",
                "row_count_prod": 100,
                "row_count_shadow": 100,
                "row_count_delta_pct": 0.0,
                "schema_divergence": [],
                "value_divergence": [],
                "sample_diff_rows": [],
                "verdict": "MATCH",
                "error": None,
            }
        )
        with patch("airflow.shadow.lifecycle.ShadowDagService.get", return_value=shadow):
            resp = test_client.get("/shadow-dags/shd_prod_20260420/reports/latest")
        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "MATCH"
