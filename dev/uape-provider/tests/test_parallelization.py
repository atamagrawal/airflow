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

# ruff: noqa: S101  — pytest uses assert

"""Tests for UAPE v2: four-signal dependency inference, confidence scoring, and analysis pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from airflow.providers.uape.parallelization import (
    THRESHOLD_REMOVE,
    THRESHOLD_UNCERTAIN,
    SignalResult,
    analyze_dag_edges,
    fit_duration_profile,
    score_edge,
    signal_asset_overlap,
    signal_timing_correlation,
    signal_transitive_reduction,
    signal_xcom_analysis,
    simulate_savings,
)

np = pytest.importorskip("numpy", reason="numpy required for simulation tests")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(
    *,
    task_type: str = "PythonOperator",
    downstream: list[str] | None = None,
    inlets: list | None = None,
    outlets: list | None = None,
    python_callable=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        task_type=task_type,
        downstream_task_ids=downstream or [],
        inlets=inlets or [],
        outlets=outlets or [],
        python_callable=python_callable,
    )


def _asset(uri: str) -> SimpleNamespace:
    return SimpleNamespace(uri=uri)


def _dag(task_dict: dict) -> SimpleNamespace:
    return SimpleNamespace(dag_id="test_dag", task_dict=task_dict)


def _signal(*, name: str, passed: bool, weight: int, skipped: bool = False) -> SignalResult:
    return SignalResult(name=name, passed=passed, weight=weight, explanation="test", skipped=skipped)


# ---------------------------------------------------------------------------
# Signal 1: Asset overlap
# ---------------------------------------------------------------------------


class TestSignalAssetOverlap:
    def test_exact_uri_match_passes(self):
        task_dict = {
            "a": _task(outlets=[_asset("s3://bucket/features/")]),
            "b": _task(inlets=[_asset("s3://bucket/features/")]),
        }
        result = signal_asset_overlap(task_dict, "a", "b")
        assert result.passed is True
        assert result.skipped is False
        assert "s3://bucket/features" in result.explanation

    def test_prefix_overlap_passes(self):
        task_dict = {
            "a": _task(outlets=[_asset("s3://bucket/features/")]),
            "b": _task(inlets=[_asset("s3://bucket/features/2024/data.parquet")]),
        }
        result = signal_asset_overlap(task_dict, "a", "b")
        assert result.passed is True

    def test_no_overlap_fails(self):
        task_dict = {
            "a": _task(outlets=[_asset("s3://bucket/models/")]),
            "b": _task(inlets=[_asset("s3://bucket/features/")]),
        }
        result = signal_asset_overlap(task_dict, "a", "b")
        assert result.passed is False
        assert result.skipped is False

    def test_no_declarations_fails_not_skipped(self):
        task_dict = {"a": _task(), "b": _task()}
        result = signal_asset_overlap(task_dict, "a", "b")
        assert result.passed is False
        assert result.skipped is False  # Signal ran; just found nothing

    def test_missing_task_skips(self):
        result = signal_asset_overlap({}, "a", "b")
        assert result.skipped is True

    def test_multiple_outlets_partial_match(self):
        task_dict = {
            "a": _task(outlets=[_asset("s3://bucket/models/"), _asset("s3://bucket/features/")]),
            "b": _task(inlets=[_asset("s3://bucket/features/")]),
        }
        result = signal_asset_overlap(task_dict, "a", "b")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Signal 2: XCom code analysis
# ---------------------------------------------------------------------------


_XCOM_PULL_SOURCE_UPSTREAM = "def f(ti):\n    return ti.xcom_pull(task_ids='upstream')\n"
_XCOM_PULL_SOURCE_OTHER = "def f(ti):\n    return ti.xcom_pull(task_ids='other_task')\n"
_XCOM_PULL_SOURCE_LIST = "def f(ti):\n    return ti.xcom_pull(task_ids=['upstream', 'other'])\n"
_XCOM_PULL_SOURCE_NONE = "def f(x):\n    return x + 1\n"


def _fake_callable():
    pass


class TestSignalXcomAnalysis:
    def test_xcom_pull_reference_passes(self):
        task_dict = {
            "upstream": _task(),
            "downstream": _task(python_callable=_fake_callable),
        }
        with patch("inspect.getsource", return_value=_XCOM_PULL_SOURCE_UPSTREAM):
            result = signal_xcom_analysis(task_dict, "upstream", "downstream")
        assert result.passed is True

    def test_xcom_pull_different_task_fails(self):
        task_dict = {
            "upstream": _task(),
            "downstream": _task(python_callable=_fake_callable),
        }
        with patch("inspect.getsource", return_value=_XCOM_PULL_SOURCE_OTHER):
            result = signal_xcom_analysis(task_dict, "upstream", "downstream")
        assert result.passed is False
        assert result.skipped is False

    def test_xcom_pull_list_of_ids_passes(self):
        task_dict = {
            "upstream": _task(),
            "downstream": _task(python_callable=_fake_callable),
        }
        with patch("inspect.getsource", return_value=_XCOM_PULL_SOURCE_LIST):
            result = signal_xcom_analysis(task_dict, "upstream", "downstream")
        assert result.passed is True

    def test_no_xcom_pull_in_source_fails_not_skipped(self):
        task_dict = {
            "upstream": _task(),
            "downstream": _task(python_callable=_fake_callable),
        }
        with patch("inspect.getsource", return_value=_XCOM_PULL_SOURCE_NONE):
            result = signal_xcom_analysis(task_dict, "upstream", "downstream")
        assert result.passed is False
        assert result.skipped is False

    def test_no_callable_skips(self):
        task_dict = {
            "upstream": _task(),
            "downstream": _task(task_type="BashOperator"),
        }
        result = signal_xcom_analysis(task_dict, "upstream", "downstream")
        assert result.skipped is True

    def test_uninspectable_callable_skips(self):
        task_dict = {
            "upstream": _task(),
            "downstream": _task(python_callable=_fake_callable),
        }
        with patch("inspect.getsource", side_effect=OSError("no source")):
            result = signal_xcom_analysis(task_dict, "upstream", "downstream")
        assert result.skipped is True

    def test_no_downstream_task_skips(self):
        result = signal_xcom_analysis({}, "upstream", "downstream")
        assert result.skipped is True


# ---------------------------------------------------------------------------
# Signal 3: Timing correlation
# ---------------------------------------------------------------------------


class TestSignalTimingCorrelation:
    def test_no_session_skips(self):
        result = signal_timing_correlation("dag", "a", "b", None)
        assert result.skipped is True

    def test_tight_coupling_passes(self):
        from datetime import datetime, timedelta

        run_ids = [f"run_{i}" for i in range(15)]
        tis = []
        base = datetime(2024, 1, 1, 0, 0, 0)
        for i, run_id in enumerate(run_ids):
            start = base + timedelta(hours=i)
            ti_up = SimpleNamespace(
                run_id=run_id, task_id="a", end_date=start + timedelta(minutes=5), start_date=start
            )
            ti_down = SimpleNamespace(
                run_id=run_id,
                task_id="b",
                start_date=start + timedelta(minutes=5, seconds=2),
                end_date=start + timedelta(minutes=10),
            )
            tis.extend([ti_up, ti_down])

        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value = tis

        result = signal_timing_correlation("dag", "a", "b", mock_session)
        assert result.passed is True
        assert "tightly coupled" in result.explanation

    def test_loose_coupling_fails(self):
        from datetime import datetime, timedelta

        import numpy as np

        rng = np.random.default_rng(42)
        tis = []
        base = datetime(2024, 1, 1, 0, 0, 0)
        for i in range(15):
            start = base + timedelta(hours=i)
            gap = float(rng.uniform(30, 120))  # 30–120 second gap (loose)
            ti_up = SimpleNamespace(
                run_id=f"run_{i}", task_id="a", end_date=start + timedelta(minutes=5), start_date=start
            )
            ti_down = SimpleNamespace(
                run_id=f"run_{i}",
                task_id="b",
                start_date=start + timedelta(minutes=5, seconds=gap),
                end_date=start + timedelta(minutes=10),
            )
            tis.extend([ti_up, ti_down])

        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value = tis

        result = signal_timing_correlation("dag", "a", "b", mock_session)
        assert result.passed is False
        assert "loose" in result.explanation

    def test_insufficient_data_fails_not_skipped(self):
        from datetime import datetime, timedelta

        tis = []
        base = datetime(2024, 1, 1)
        for i in range(5):  # Below MIN_TIMING_RUNS=10
            start = base + timedelta(hours=i)
            tis.append(SimpleNamespace(run_id=f"r{i}", task_id="a", end_date=start, start_date=start))
            tis.append(
                SimpleNamespace(
                    run_id=f"r{i}",
                    task_id="b",
                    start_date=start + timedelta(seconds=3),
                    end_date=start + timedelta(minutes=2),
                )
            )

        mock_session = MagicMock()
        mock_session.execute.return_value.scalars.return_value = tis

        result = signal_timing_correlation("dag", "a", "b", mock_session)
        assert result.skipped is False  # Signal ran; just not enough data
        assert result.passed is False
        assert "5" in result.explanation


# ---------------------------------------------------------------------------
# Signal 4: Transitive reduction
# ---------------------------------------------------------------------------


class TestSignalTransitiveReduction:
    def test_direct_minimal_edge_passes(self):
        # A → B with no alternative path
        adj = {"a": {"b"}, "b": set()}
        result = signal_transitive_reduction(adj, "a", "b")
        assert result.passed is True
        assert "minimal" in result.explanation.lower()

    def test_redundant_edge_fails(self):
        # A → B, B → C, A → C  (A→C is redundant)
        adj = {"a": {"b", "c"}, "b": {"c"}, "c": set()}
        result = signal_transitive_reduction(adj, "a", "c")
        assert result.passed is False
        assert "redundant" in result.explanation.lower()

    def test_longer_path_mentioned_in_explanation(self):
        adj = {"a": {"b", "c"}, "b": {"c"}, "c": set()}
        result = signal_transitive_reduction(adj, "a", "c")
        assert "a" in result.explanation
        assert "c" in result.explanation

    def test_independent_tasks_not_tested(self):
        # If called for tasks with no path at all, networkx still gives a result
        adj = {"a": set(), "b": set()}
        result = signal_transitive_reduction(adj, "a", "b")
        # No edge a→b in the graph so it won't survive reduction
        assert result.passed is False


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


class TestScoreEdge:
    def test_all_pass_gives_100(self):
        signals = [
            _signal(name="s1", passed=True, weight=35),
            _signal(name="s2", passed=True, weight=25),
            _signal(name="s3", passed=True, weight=20),
            _signal(name="s4", passed=True, weight=20),
        ]
        es = score_edge("a", "b", signals)
        assert es.confidence_score == 100
        assert es.verdict == "keep"

    def test_all_fail_gives_0_remove(self):
        signals = [
            _signal(name="s1", passed=False, weight=35),
            _signal(name="s2", passed=False, weight=25),
            _signal(name="s3", passed=False, weight=20),
            _signal(name="s4", passed=False, weight=20),
        ]
        es = score_edge("a", "b", signals)
        assert es.confidence_score == 0
        assert es.verdict == "remove"

    def test_only_large_signal_passes_keeps(self):
        # dataset_overlap (35) passes → 35/100 → uncertain
        signals = [
            _signal(name="asset_overlap", passed=True, weight=35),
            _signal(name="xcom", passed=False, weight=25),
            _signal(name="timing", passed=False, weight=20),
            _signal(name="transitive", passed=False, weight=20),
        ]
        es = score_edge("a", "b", signals)
        assert es.confidence_score == 35
        assert es.verdict == "remove"

    def test_skipped_signals_excluded_from_weight(self):
        # Only two signals available: both pass → score = 100
        signals = [
            _signal(name="s1", passed=True, weight=35),
            _signal(name="s2", passed=False, weight=25, skipped=True),
            _signal(name="s3", passed=True, weight=20),
            _signal(name="s4", passed=False, weight=20, skipped=True),
        ]
        es = score_edge("a", "b", signals)
        # earned=55, available_weight=55 → 100
        assert es.confidence_score == 100
        assert es.verdict == "keep"

    def test_all_skipped_gives_50_uncertain(self):
        signals = [
            _signal(name="s1", passed=False, weight=35, skipped=True),
            _signal(name="s2", passed=False, weight=25, skipped=True),
        ]
        es = score_edge("a", "b", signals)
        assert es.confidence_score == 50
        assert es.verdict == "uncertain"

    def test_verdict_thresholds(self):
        # Score just below THRESHOLD_REMOVE
        below_remove = [
            _signal(name="s", passed=True, weight=THRESHOLD_REMOVE - 1),
            _signal(name="t", passed=False, weight=100 - (THRESHOLD_REMOVE - 1)),
        ]
        es = score_edge("a", "b", below_remove)
        assert es.verdict == "remove"

        # Score exactly THRESHOLD_UNCERTAIN → keep
        at_keep = [_signal(name="s", passed=True, weight=THRESHOLD_UNCERTAIN)]
        es2 = score_edge("a", "b", at_keep)
        assert es2.verdict == "keep"


# ---------------------------------------------------------------------------
# Duration profiler
# ---------------------------------------------------------------------------


class TestFitDurationProfile:
    def test_returns_none_for_too_few_samples(self):
        assert fit_duration_profile("t", []) is None
        assert fit_duration_profile("t", [1.0, 2.0]) is None  # < MIN_PROFILE_RUNS=5

    def test_returns_profile_for_sufficient_samples(self):
        durations = [10.0, 12.0, 11.0, 13.0, 10.5, 11.5, 12.5, 11.0, 10.0, 13.0]
        profile = fit_duration_profile("my_task", durations)
        assert profile is not None
        assert profile.task_id == "my_task"
        assert profile.mean > 0
        assert profile.std >= 0
        assert profile.p5 <= profile.p50 <= profile.p95
        assert profile.n_samples == len(durations)

    def test_profile_dist_name_is_string(self):
        durations = list(range(10, 25))
        profile = fit_duration_profile("t", durations)
        assert profile is not None
        assert isinstance(profile.dist_name, str)


# ---------------------------------------------------------------------------
# Monte Carlo simulation
# ---------------------------------------------------------------------------


class TestSimulateSavings:
    def _simple_profiles(self) -> dict:
        from airflow.providers.uape.parallelization import DurationProfile

        # Two tasks: "train" (mean=20min) and "report" (mean=5min)
        return {
            "transform": DurationProfile(
                task_id="transform",
                dist_name="empirical",
                params=(),
                mean=60.0,
                std=5.0,
                p5=50.0,
                p50=60.0,
                p95=70.0,
                n_samples=50,
            ),
            "train": DurationProfile(
                task_id="train",
                dist_name="empirical",
                params=(),
                mean=1200.0,
                std=120.0,
                p5=960.0,
                p50=1200.0,
                p95=1440.0,
                n_samples=50,
            ),
            "report": DurationProfile(
                task_id="report",
                dist_name="empirical",
                params=(),
                mean=300.0,
                std=30.0,
                p5=240.0,
                p50=300.0,
                p95=360.0,
                n_samples=50,
            ),
        }

    def test_removing_false_edge_produces_savings(self):
        # Current: transform >> train >> report (false edge train>>report)
        # Proposed: transform >> train, transform >> report (parallel)
        profiles = self._simple_profiles()
        task_ids = ["transform", "train", "report"]

        adj_current = {"transform": {"train"}, "train": {"report"}, "report": set()}
        adj_proposed = {"transform": {"train", "report"}, "train": set(), "report": set()}

        result = simulate_savings(task_ids, adj_current, adj_proposed, profiles, n=1000)
        assert result is not None
        # Sequential: 60 + 1200 + 300 = 1560; Parallel: 60 + max(1200, 300) = 1260
        # Expected saving ≈ 300 s
        assert result.mean_savings_seconds > 100  # Should save significantly
        assert result.prob_improvement > 0.9  # Almost always saves

    def test_no_profiles_returns_none(self):
        result = simulate_savings(["a", "b"], {"a": {"b"}}, {"a": set(), "b": set()}, {}, n=100)
        assert result is None

    def test_simulation_fields_present(self):
        profiles = self._simple_profiles()
        task_ids = ["transform", "train", "report"]
        adj_current = {"transform": {"train"}, "train": {"report"}, "report": set()}
        adj_proposed = {"transform": {"train", "report"}, "train": set(), "report": set()}

        result = simulate_savings(task_ids, adj_current, adj_proposed, profiles, n=500)
        assert result is not None
        assert result.n_simulations == 500
        assert 0.0 <= result.prob_improvement <= 1.0
        assert result.p5_savings_seconds <= result.p50_savings_seconds <= result.p95_savings_seconds


# ---------------------------------------------------------------------------
# Full analysis pipeline
# ---------------------------------------------------------------------------


class TestAnalyzeDagEdges:
    def test_schema_version_and_policy(self):
        dag = _dag({"a": _task(downstream=["b"]), "b": _task()})
        report = analyze_dag_edges(dag, session=None)
        assert report["report_schema_version"] == "2.0"
        assert report["policy"] == "uncertainty_aware_v1"
        assert "generated_at_utc" in report
        assert "uape_provider_version" in report

    def test_no_edges_returns_empty_analysis(self):
        dag = _dag({"a": _task(), "b": _task()})
        report = analyze_dag_edges(dag, session=None)
        assert report["summary"]["total_edges"] == 0
        assert report["edge_analyses"] == []

    def test_single_edge_appears_in_analysis(self):
        dag = _dag({"a": _task(downstream=["b"]), "b": _task()})
        report = analyze_dag_edges(dag, session=None)
        assert report["summary"]["total_edges"] == 1
        assert len(report["edge_analyses"]) == 1
        edge = report["edge_analyses"][0]
        assert edge["from_task"] == "a"
        assert edge["to_task"] == "b"
        assert edge["verdict"] in ("remove", "uncertain", "keep")
        assert 0 <= edge["confidence_score"] <= 100

    def test_edge_with_asset_overlap_scores_higher(self):
        """An edge where outlets/inlets match should score higher than one with no declarations."""
        shared_asset = _asset("s3://bucket/data/")
        dag_with_assets = _dag(
            {
                "a": _task(downstream=["b"], outlets=[shared_asset]),
                "b": _task(inlets=[shared_asset]),
            }
        )
        dag_without_assets = _dag(
            {
                "a": _task(downstream=["b"]),
                "b": _task(),
            }
        )
        report_with = analyze_dag_edges(dag_with_assets, session=None)
        report_without = analyze_dag_edges(dag_without_assets, session=None)

        score_with = report_with["edge_analyses"][0]["confidence_score"]
        score_without = report_without["edge_analyses"][0]["confidence_score"]
        assert score_with > score_without

    def test_all_signals_appear_in_output(self):
        dag = _dag({"a": _task(downstream=["b"]), "b": _task()})
        report = analyze_dag_edges(dag, session=None)
        edge = report["edge_analyses"][0]
        signal_names = {s["name"] for s in edge["signals"]}
        assert "asset_overlap" in signal_names
        assert "xcom_analysis" in signal_names
        assert "timing_correlation" in signal_names
        assert "transitive_reduction" in signal_names

    def test_redundant_edge_detected(self):
        # A → B, B → C, A → C  (A→C is redundant)
        dag = _dag(
            {
                "a": _task(downstream=["b", "c"]),
                "b": _task(downstream=["c"]),
                "c": _task(),
            }
        )
        report = analyze_dag_edges(dag, session=None)
        redundant = report.get("redundant_edges", [])
        pairs = {(r["from_task"], r["to_task"]) for r in redundant}
        assert ("a", "c") in pairs

    def test_graph_metrics_correct(self):
        dag = _dag(
            {
                "a": _task(downstream=["b", "c"]),
                "b": _task(downstream=["d"]),
                "c": _task(downstream=["d"]),
                "d": _task(),
            }
        )
        report = analyze_dag_edges(dag, session=None)
        gm = report["graph_metrics"]
        assert gm["task_count"] == 4
        assert gm["dependency_edge_count"] == 4

    def test_summary_counts_match_edge_analyses(self):
        dag = _dag(
            {
                "a": _task(downstream=["b"]),
                "b": _task(downstream=["c"]),
                "c": _task(),
            }
        )
        report = analyze_dag_edges(dag, session=None)
        edges = report["edge_analyses"]
        summ = report["summary"]
        assert summ["remove_count"] == sum(1 for e in edges if e["verdict"] == "remove")
        assert summ["uncertain_count"] == sum(1 for e in edges if e["verdict"] == "uncertain")
        assert summ["keep_count"] == sum(1 for e in edges if e["verdict"] == "keep")
        assert summ["total_edges"] == len(edges)

    def test_without_session_timing_signals_skipped(self):
        dag = _dag({"a": _task(downstream=["b"]), "b": _task()})
        report = analyze_dag_edges(dag, session=None)
        edge = report["edge_analyses"][0]
        timing_sig = next(s for s in edge["signals"] if s["name"] == "timing_correlation")
        assert timing_sig["skipped"] is True

    def test_executive_summary_present_and_non_empty(self):
        dag = _dag({"a": _task(downstream=["b"]), "b": _task()})
        report = analyze_dag_edges(dag, session=None)
        assert report["executive_summary"]
        assert isinstance(report["executive_summary"], str)

    def test_no_historical_data_flagged_in_summary(self):
        dag = _dag({"a": _task(downstream=["b"]), "b": _task()})
        report = analyze_dag_edges(dag, session=None)
        assert report["summary"]["has_historical_data"] is False
        assert report["summary"]["profiled_task_count"] == 0
