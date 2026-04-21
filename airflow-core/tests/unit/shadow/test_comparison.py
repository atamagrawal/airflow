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
"""Tests for the Shadow DAG Comparison Engine (AIP-09)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from airflow.shadow.comparison import (
    ComparisonEngine,
    ComparisonReport,
    Verdict,
    _detect_schema_divergence,
    _detect_value_divergence,
    _determine_verdict,
    _load_jsonl,
)
from airflow.shadow.sink_proxy import ShadowContext


@pytest.fixture()
def shadow_ctx(tmp_path: Path) -> ShadowContext:
    sink_root = tmp_path / "shadow" / "shd_test" / "run_001"
    sink_root.mkdir(parents=True)
    return ShadowContext(
        shadow_id="shd_test",
        production_dag_id="prod_dag",
        run_id="run_001",
        sink_root=sink_root,
    )


@pytest.fixture()
def mock_shadow_dag():
    dag = MagicMock()
    dag.shadow_id = "shd_test"
    dag.production_dag_id = "prod_dag"
    dag.divergence_alert_pct = 0.05
    return dag


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


class TestLoadJsonl:
    def test_load_existing_file(self, tmp_path: Path):
        p = tmp_path / "out.jsonl"
        _write_jsonl(p, [{"a": 1}, {"a": 2}])
        rows = _load_jsonl(p)
        assert rows == [{"a": 1}, {"a": 2}]

    def test_missing_file_returns_empty(self, tmp_path: Path):
        rows = _load_jsonl(tmp_path / "nonexistent.jsonl")
        assert rows == []

    def test_malformed_line_skipped(self, tmp_path: Path):
        p = tmp_path / "out.jsonl"
        p.write_text('{"a":1}\nNOT JSON\n{"a":3}\n')
        rows = _load_jsonl(p)
        assert len(rows) == 2


class TestSchemaDivergence:
    def test_no_divergence(self):
        prod = [{"a": 1, "b": 2}]
        shadow = [{"a": 1, "b": 2}]
        assert _detect_schema_divergence(prod, shadow) == []

    def test_added_column(self):
        prod = [{"a": 1}]
        shadow = [{"a": 1, "b": 2}]
        diffs = _detect_schema_divergence(prod, shadow)
        assert any(d.column == "b" and d.change == "added" for d in diffs)

    def test_removed_column(self):
        prod = [{"a": 1, "b": 2}]
        shadow = [{"a": 1}]
        diffs = _detect_schema_divergence(prod, shadow)
        assert any(d.column == "b" and d.change == "removed" for d in diffs)

    def test_empty_rows(self):
        assert _detect_schema_divergence([], []) == []


class TestDetermineVerdict:
    @pytest.mark.parametrize(
        "delta_pct, schema_diffs, threshold, expected",
        [
            (0.0, [], 0.05, Verdict.MATCH),
            (3.0, [], 0.05, Verdict.WITHIN_THRESHOLD),
            (10.0, [], 0.05, Verdict.DIVERGED),
            (0.0, [MagicMock()], 0.05, Verdict.DIVERGED),
        ],
    )
    def test_verdict_cases(self, delta_pct, schema_diffs, threshold, expected):
        assert _determine_verdict(delta_pct, schema_diffs, threshold) == expected


class TestComparisonEngine:
    def test_identical_outputs_match(
        self, shadow_ctx: ShadowContext, mock_shadow_dag, tmp_path: Path
    ):
        rows = [{"id": 1, "val": "a"}, {"id": 2, "val": "b"}]
        shadow_output = shadow_ctx.sink_root / "task" / "output.jsonl"
        _write_jsonl(shadow_output, rows)

        prod_root = tmp_path / "prod_run"
        prod_output = prod_root / "task" / "output.jsonl"
        _write_jsonl(prod_output, rows)

        engine = ComparisonEngine()
        report = engine.compare(shadow_ctx, mock_shadow_dag, prod_sink_root=prod_root)
        assert report.verdict == Verdict.MATCH
        assert report.row_count_prod == 2
        assert report.row_count_shadow == 2
        assert report.row_count_delta_pct == 0.0

    def test_empty_shadow_output_diverged(
        self, shadow_ctx: ShadowContext, mock_shadow_dag, tmp_path: Path
    ):
        prod_root = tmp_path / "prod_run"
        prod_output = prod_root / "task" / "output.jsonl"
        _write_jsonl(prod_output, [{"id": i} for i in range(10)])

        # shadow has no output files
        engine = ComparisonEngine()
        report = engine.compare(shadow_ctx, mock_shadow_dag, prod_sink_root=prod_root)
        assert report.verdict in (Verdict.DIVERGED, Verdict.SHADOW_FAILED)

    def test_read_error_returns_shadow_failed(
        self, shadow_ctx: ShadowContext, mock_shadow_dag
    ):
        engine = ComparisonEngine()
        with patch.object(engine, "_read_shadow_rows", side_effect=RuntimeError("disk error")):
            report = engine.compare(shadow_ctx, mock_shadow_dag)
        assert report.verdict == Verdict.SHADOW_FAILED
        assert report.error is not None

    def test_report_to_dict_roundtrip(self):
        report = ComparisonReport(
            run_id="r1",
            shadow_id="s1",
            row_count_prod=10,
            row_count_shadow=10,
            row_count_delta_pct=0.0,
            verdict=Verdict.MATCH,
        )
        data = report.to_dict()
        restored = ComparisonReport.from_dict(data)
        assert restored.verdict == Verdict.MATCH
        assert restored.run_id == "r1"
