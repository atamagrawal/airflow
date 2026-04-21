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
"""Tests for the ShadowDag ORM model (AIP-09)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from airflow._shared.timezones import timezone
from airflow.models.shadow_dag import (
    InvalidShadowTransition,
    ShadowDag,
    ShadowDagNotFound,
    ShadowDagStatus,
    _VALID_TRANSITIONS,
)


class TestShadowDagStatus:
    def test_all_statuses_have_transitions(self):
        for status in ShadowDagStatus:
            assert status in _VALID_TRANSITIONS

    def test_cleaned_up_is_terminal(self):
        assert not ShadowDagStatus.CLEANED_UP.can_transition_to(ShadowDagStatus.REGISTERED)
        assert not ShadowDagStatus.CLEANED_UP.can_transition_to(ShadowDagStatus.ACTIVE)

    def test_registered_can_become_active_or_discarded(self):
        assert ShadowDagStatus.REGISTERED.can_transition_to(ShadowDagStatus.ACTIVE)
        assert ShadowDagStatus.REGISTERED.can_transition_to(ShadowDagStatus.DISCARDED)
        assert not ShadowDagStatus.REGISTERED.can_transition_to(ShadowDagStatus.PROMOTED)


class TestShadowDagModel:
    @pytest.fixture()
    def shadow(self) -> ShadowDag:
        now = timezone.utcnow()
        return ShadowDag(
            shadow_id="shd_test_dag_20260420",
            production_dag_id="test_dag",
            candidate_dag_id="test_dag_v2",
            status=ShadowDagStatus.REGISTERED.value,
            ttl_days=7,
            divergence_alert_pct=0.05,
            created_at=now,
            expires_at=now + timedelta(days=7),
        )

    def test_status_enum_property(self, shadow: ShadowDag):
        assert shadow.status_enum == ShadowDagStatus.REGISTERED

    def test_status_enum_setter(self, shadow: ShadowDag):
        shadow.status_enum = ShadowDagStatus.ACTIVE
        assert shadow.status == "active"

    def test_is_active(self, shadow: ShadowDag):
        shadow.status = ShadowDagStatus.ACTIVE.value
        assert shadow.is_active()
        shadow.status = ShadowDagStatus.REGISTERED.value
        assert not shadow.is_active()

    def test_is_terminal(self, shadow: ShadowDag):
        shadow.status_enum = ShadowDagStatus.PROMOTED
        assert shadow.is_terminal()
        shadow.status_enum = ShadowDagStatus.CLEANED_UP
        assert shadow.is_terminal()
        shadow.status_enum = ShadowDagStatus.ACTIVE
        assert not shadow.is_terminal()

    def test_repr(self, shadow: ShadowDag):
        r = repr(shadow)
        assert "shd_test_dag_20260420" in r
        assert "registered" in r


class TestShadowDagExceptions:
    def test_invalid_transition_message(self):
        exc = InvalidShadowTransition(ShadowDagStatus.CLEANED_UP, ShadowDagStatus.ACTIVE)
        assert "cleaned_up" in str(exc)
        assert "active" in str(exc)

    def test_not_found_message(self):
        exc = ShadowDagNotFound("shd_missing")
        assert "shd_missing" in str(exc)
