#
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
"""Tests for the Shadow DAG lifecycle service (AIP-09)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from airflow._shared.timezones import timezone
from airflow.models.shadow_dag import InvalidShadowTransition, ShadowDag, ShadowDagNotFound, ShadowDagStatus
from airflow.shadow.lifecycle import ShadowDagService, _parse_ttl


class TestParseTtl:
    @pytest.mark.parametrize(
        "ttl, expected",
        [
            ("1d", 1),
            ("7d", 7),
            ("14d", 14),
            ("3", 3),
        ],
    )
    def test_valid_ttl(self, ttl, expected):
        assert _parse_ttl(ttl) == expected

    @pytest.mark.parametrize("ttl", ["0d", "15d", "-1d", "abc"])
    def test_invalid_ttl(self, ttl):
        with pytest.raises(ValueError):
            _parse_ttl(ttl)


class TestShadowDagService:
    """Unit tests using a mock session — no DB required."""

    @pytest.fixture()
    def service(self):
        return ShadowDagService()

    @pytest.fixture()
    def mock_session(self, mocker):
        session = mocker.MagicMock()
        session.get.return_value = None  # no existing record by default
        return session

    def _make_shadow(self, status: ShadowDagStatus = ShadowDagStatus.REGISTERED) -> ShadowDag:
        now = timezone.utcnow()
        shadow = ShadowDag(
            shadow_id="shd_prod_20260420",
            production_dag_id="prod_dag",
            candidate_dag_id="candidate_dag",
            status=status.value,
            ttl_days=7,
            divergence_alert_pct=0.05,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )
        return shadow

    def test_create_new_shadow(self, service, mock_session):
        with patch("airflow.shadow.lifecycle.Stats"):
            shadow = service.create(
                production_dag_id="prod_dag",
                candidate_dag_id="candidate_dag",
                ttl="7d",
                session=mock_session,
            )
        mock_session.add.assert_called_once()
        assert shadow.production_dag_id == "prod_dag"
        assert shadow.candidate_dag_id == "candidate_dag"
        assert shadow.ttl_days == 7

    def test_create_returns_existing(self, service, mock_session):
        existing = self._make_shadow()
        mock_session.get.return_value = existing
        result = service.create(
            production_dag_id="prod_dag",
            candidate_dag_id="other",
            session=mock_session,
        )
        assert result is existing
        mock_session.add.assert_not_called()

    def test_get_not_found(self, service, mock_session):
        mock_session.get.return_value = None
        with pytest.raises(ShadowDagNotFound):
            service.get("nonexistent", session=mock_session)

    def test_get_found(self, service, mock_session):
        shadow = self._make_shadow()
        mock_session.get.return_value = shadow
        result = service.get(shadow.shadow_id, session=mock_session)
        assert result is shadow

    def test_valid_transition(self, service, mock_session):
        shadow = self._make_shadow(ShadowDagStatus.REGISTERED)
        mock_session.get.return_value = shadow
        with patch("airflow.shadow.lifecycle.Stats"):
            result = service.transition(shadow.shadow_id, new_status=ShadowDagStatus.ACTIVE, session=mock_session)
        assert result.status == ShadowDagStatus.ACTIVE.value

    def test_invalid_transition_raises(self, service, mock_session):
        shadow = self._make_shadow(ShadowDagStatus.CLEANED_UP)
        mock_session.get.return_value = shadow
        with pytest.raises(InvalidShadowTransition):
            service.transition(shadow.shadow_id, new_status=ShadowDagStatus.ACTIVE, session=mock_session)

    def test_promote_from_review(self, service, mock_session):
        shadow = self._make_shadow(ShadowDagStatus.REVIEW)
        mock_session.get.return_value = shadow
        with patch("airflow.shadow.lifecycle.Stats"):
            result = service.promote(shadow.shadow_id, session=mock_session)
        assert result.status == ShadowDagStatus.PROMOTED.value

    def test_discard_from_active(self, service, mock_session):
        shadow = self._make_shadow(ShadowDagStatus.ACTIVE)
        mock_session.get.return_value = shadow
        with patch("airflow.shadow.lifecycle.Stats"):
            result = service.discard(shadow.shadow_id, session=mock_session)
        assert result.status == ShadowDagStatus.DISCARDED.value

    def test_cleanup_expired_transitions_to_cleaned_up(self, service, mock_session):
        now = timezone.utcnow()
        shadows = [
            ShadowDag(
                shadow_id=f"shd_{i}",
                production_dag_id="p",
                candidate_dag_id="c",
                status=ShadowDagStatus.REVIEW.value,
                ttl_days=1,
                divergence_alert_pct=0.05,
                created_at=now - timedelta(days=3),
                expires_at=now - timedelta(days=1),  # already expired
            )
            for i in range(2)
        ]
        mock_session.scalars.return_value = shadows
        with patch("airflow.shadow.lifecycle.Stats"):
            count = service.cleanup_expired(session=mock_session)
        assert count == 2
        for s in shadows:
            assert s.status == ShadowDagStatus.CLEANED_UP.value


class TestShadowDagStatusTransitions:
    @pytest.mark.parametrize(
        "current, target, allowed",
        [
            (ShadowDagStatus.REGISTERED, ShadowDagStatus.ACTIVE, True),
            (ShadowDagStatus.REGISTERED, ShadowDagStatus.DISCARDED, True),
            (ShadowDagStatus.REGISTERED, ShadowDagStatus.PROMOTED, False),
            (ShadowDagStatus.ACTIVE, ShadowDagStatus.REVIEW, True),
            (ShadowDagStatus.ACTIVE, ShadowDagStatus.DISCARDED, True),
            (ShadowDagStatus.REVIEW, ShadowDagStatus.PROMOTED, True),
            (ShadowDagStatus.REVIEW, ShadowDagStatus.DISCARDED, True),
            (ShadowDagStatus.CLEANED_UP, ShadowDagStatus.ACTIVE, False),
        ],
    )
    def test_can_transition_to(self, current, target, allowed):
        assert current.can_transition_to(target) is allowed
