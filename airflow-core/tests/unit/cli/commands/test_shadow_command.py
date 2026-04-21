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
"""Tests for the Shadow DAG CLI commands (AIP-09)."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest

from airflow._shared.timezones import timezone
from airflow.cli.commands.shadow_command import (
    shadow_create,
    shadow_discard,
    shadow_list,
    shadow_promote,
    shadow_report,
)
from airflow.models.shadow_dag import ShadowDag, ShadowDagStatus


def _make_shadow(status=ShadowDagStatus.REGISTERED):
    now = timezone.utcnow()
    return ShadowDag(
        shadow_id="shd_prod_dag_20260420",
        production_dag_id="prod_dag",
        candidate_dag_id="prod_dag_v2",
        status=status.value,
        ttl_days=7,
        divergence_alert_pct=0.05,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )


def _args(**kwargs) -> argparse.Namespace:
    defaults = {
        "candidate_dag_id": "prod_dag_v2",
        "candidate_file": None,
        "divergence_alert": 0.05,
        "notify": None,
        "output": "table",
        "production_dag": "prod_dag",
        "shadow_id": "shd_prod_dag_20260420",
        "status": None,
        "ttl": "7d",
        "verbose": False,
        "yes": True,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestShadowCreate:
    @patch("airflow.cli.commands.shadow_command.create_session")
    @patch("airflow.shadow.lifecycle.Stats")
    def test_create_calls_service(self, _mock_stats, mock_session_ctx):
        shadow = _make_shadow()
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.return_value = session

        with patch("airflow.shadow.lifecycle.ShadowDagService.create", return_value=shadow):
            shadow_create(_args())

    def test_missing_candidate_file_raises(self, tmp_path):
        with pytest.raises(SystemExit):
            shadow_create(_args(candidate_file=str(tmp_path / "nonexistent.py")))


class TestShadowList:
    @patch("airflow.cli.commands.shadow_command.create_session")
    def test_list_calls_service(self, mock_session_ctx):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.return_value = session

        with patch("airflow.shadow.lifecycle.ShadowDagService.list", return_value=[]):
            shadow_list(_args())

    @patch("airflow.cli.commands.shadow_command.create_session")
    def test_invalid_status_raises(self, mock_session_ctx):
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.return_value = session

        with pytest.raises(SystemExit):
            shadow_list(_args(status="not_a_status"))


class TestShadowReport:
    @patch("airflow.cli.commands.shadow_command.create_session")
    def test_report_shows_no_report_message(self, mock_session_ctx):
        shadow = _make_shadow()
        shadow.last_comparison_json = None
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.return_value = session

        with patch("airflow.shadow.lifecycle.ShadowDagService.get", return_value=shadow):
            shadow_report(_args())

    @patch("airflow.cli.commands.shadow_command.create_session")
    def test_report_parses_comparison_json(self, mock_session_ctx):
        shadow = _make_shadow()
        shadow.last_comparison_json = json.dumps(
            {
                "run_id": "r1",
                "shadow_id": "shd_prod_dag_20260420",
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
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.return_value = session

        with patch("airflow.shadow.lifecycle.ShadowDagService.get", return_value=shadow):
            shadow_report(_args())


class TestShadowPromote:
    @patch("airflow.cli.commands.shadow_command.create_session")
    @patch("airflow.shadow.lifecycle.Stats")
    def test_promote_calls_service(self, _mock_stats, mock_session_ctx):
        shadow = _make_shadow(ShadowDagStatus.REVIEW)
        shadow.status = ShadowDagStatus.PROMOTED.value
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.return_value = session

        with patch("airflow.shadow.lifecycle.ShadowDagService.promote", return_value=shadow):
            shadow_promote(_args())


class TestShadowDiscard:
    @patch("airflow.cli.commands.shadow_command.create_session")
    @patch("airflow.shadow.lifecycle.Stats")
    def test_discard_with_yes_flag(self, _mock_stats, mock_session_ctx):
        shadow = _make_shadow(ShadowDagStatus.ACTIVE)
        shadow.status = ShadowDagStatus.DISCARDED.value
        session = MagicMock()
        session.__enter__ = MagicMock(return_value=session)
        session.__exit__ = MagicMock(return_value=False)
        mock_session_ctx.return_value = session

        with patch("airflow.shadow.lifecycle.ShadowDagService.discard", return_value=shadow):
            shadow_discard(_args(yes=True))
