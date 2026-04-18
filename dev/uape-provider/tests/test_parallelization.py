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

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from airflow.providers.uape.parallelization import (
    _CLEAR_T1_TYPES,
    _CLEAR_T2_TYPES,
    UAPE_EXTRA_CLEAR_TYPES_ENV,
    UAPE_FULL_PAIR_ANALYSIS_TASK_LIMIT,
    _parse_extra_clear_types,
    analyze_serialized_dag,
)


@pytest.fixture
def patch_not_mapped():
    with patch("airflow.serialization.definitions.mappedoperator.is_mapped", return_value=False):
        yield


def _dag(task_dict: dict) -> SimpleNamespace:
    return SimpleNamespace(dag_id="test_dag", task_dict=task_dict)


def _task(task_type: str, downstream: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(downstream_task_ids=downstream or [], task_type=task_type)


# ---------------------------------------------------------------------------
# Schema and policy metadata
# ---------------------------------------------------------------------------


def test_report_schema_version_and_policy(patch_not_mapped):
    dag = _dag({"a": _task("EmptyOperator"), "b": _task("EmptyOperator")})
    report = analyze_serialized_dag(dag)
    assert report["report_schema_version"] == "1.2"
    assert report["policy"] == "conservative_v2"
    assert "generated_at_utc" in report
    assert "clear_operator_allowlist_tiers" in report


# ---------------------------------------------------------------------------
# Tier 1: trivial operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op_type", sorted(_CLEAR_T1_TYPES))
def test_t1_operators_get_high_confidence_hint(op_type, patch_not_mapped):
    """Each T1 operator paired with another T1 operator produces a high-confidence hint."""
    dag = _dag({"a": _task(op_type), "b": _task(op_type)})
    report = analyze_serialized_dag(dag)
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1, f"{op_type}: expected 1 hint, got {len(hints)}"
    assert hints[0]["confidence"] == "high"
    assert hints[0]["task_a_clear_tier"] == "t1_trivial"
    assert hints[0]["task_b_clear_tier"] == "t1_trivial"


def test_t1_empty_operator_pair_emits_hint(patch_not_mapped):
    """Two EmptyOperators with no dependency path get a high-confidence overlap hint."""
    dag = _dag({"a": _task("EmptyOperator"), "b": _task("EmptyOperator")})
    report = analyze_serialized_dag(dag)
    assert report["graph_metrics"]["task_count"] == 2
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1
    hint = hints[0]
    assert {hint["task_a"], hint["task_b"]} == {"a", "b"}
    assert hint["confidence"] == "high"
    assert hint["recommendation_key"] == "graph_independent_clear_allowlist_pair"
    assert "recommendation_summary" in hint
    assert "suggested_next_steps" in hint
    assert report["executive_summary"]


# ---------------------------------------------------------------------------
# Tier 2: computation / common operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op_type", sorted(_CLEAR_T2_TYPES))
def test_t2_operators_produce_hints(op_type, patch_not_mapped):
    """Each T2 operator paired independently should yield an overlap hint."""
    dag = _dag({"a": _task(op_type), "b": _task(op_type)})
    report = analyze_serialized_dag(dag)
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1, f"{op_type}: expected 1 hint, got {len(hints)}"
    assert hints[0]["task_a_clear_tier"] == "t2_computation"
    assert hints[0]["task_b_clear_tier"] == "t2_computation"


def test_t2_confidence_is_medium_high_for_t2_pair(patch_not_mapped):
    dag = _dag({"a": _task("PythonOperator"), "b": _task("BashOperator")})
    report = analyze_serialized_dag(dag)
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1
    assert hints[0]["confidence"] == "medium_high"


def test_t1_t2_mixed_pair_confidence_is_medium_high(patch_not_mapped):
    """T1+T2 pair uses the lower tier's confidence (medium_high from T2)."""
    dag = _dag({"a": _task("EmptyOperator"), "b": _task("PythonOperator")})
    report = analyze_serialized_dag(dag)
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1
    assert hints[0]["confidence"] == "medium_high"
    assert {hints[0]["task_a_clear_tier"], hints[0]["task_b_clear_tier"]} == {"t1_trivial", "t2_computation"}


def test_python_operator_with_dependency_no_hint(patch_not_mapped):
    """When there IS a dependency, no parallel hint should be emitted."""
    dag = _dag({"a": _task("PythonOperator", downstream=["b"]), "b": _task("PythonOperator")})
    report = analyze_serialized_dag(dag)
    assert report["clear_task_overlap_hints"] == []
    assert report["structurally_independent_pair_count"] == 0


def test_bash_operator_no_hint_when_dependent(patch_not_mapped):
    dag = _dag({"a": _task("BashOperator", downstream=["b"]), "b": _task("BashOperator")})
    report = analyze_serialized_dag(dag)
    assert report["clear_task_overlap_hints"] == []


def test_time_sensor_operators_get_hints(patch_not_mapped):
    dag = _dag({"a": _task("TimeSensor"), "b": _task("DateTimeSensor")})
    report = analyze_serialized_dag(dag)
    assert len(report["clear_task_overlap_hints"]) == 1


# ---------------------------------------------------------------------------
# Tier 3: user-extended types
# ---------------------------------------------------------------------------


def test_user_extra_clear_types_via_argument(patch_not_mapped):
    """Operator types passed via extra_clear_types get T3 tier."""
    dag = _dag({"a": _task("MyCustomOperator"), "b": _task("MyCustomOperator")})
    report = analyze_serialized_dag(dag, extra_clear_types={"MyCustomOperator"})
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1
    assert hints[0]["confidence"] == "medium"
    assert hints[0]["task_a_clear_tier"] == "t3_user_extended"
    assert hints[0]["task_b_clear_tier"] == "t3_user_extended"


def test_user_extra_clear_types_via_env(patch_not_mapped, monkeypatch):
    """Operator types set in UAPE_EXTRA_CLEAR_OPERATOR_TYPES env var get T3 tier."""
    monkeypatch.setenv(UAPE_EXTRA_CLEAR_TYPES_ENV, "EnvCustomOp,AnotherOp")
    dag = _dag({"a": _task("EnvCustomOp"), "b": _task("AnotherOp")})
    report = analyze_serialized_dag(dag)
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1
    assert hints[0]["confidence"] == "medium"


def test_user_extra_clear_types_argument_overrides_env(patch_not_mapped, monkeypatch):
    """When extra_clear_types argument is provided, env var is ignored."""
    monkeypatch.setenv(UAPE_EXTRA_CLEAR_TYPES_ENV, "EnvOp")
    dag = _dag({"a": _task("EnvOp"), "b": _task("EnvOp")})
    # Pass an empty set to explicitly suppress env-var lookup
    report = analyze_serialized_dag(dag, extra_clear_types=set())
    # EnvOp should be opaque now — env var was bypassed
    assert report["clear_task_overlap_hints"] == []
    # But OpArg type should work
    dag2 = _dag({"a": _task("ArgOp"), "b": _task("ArgOp")})
    report2 = analyze_serialized_dag(dag2, extra_clear_types={"ArgOp"})
    assert len(report2["clear_task_overlap_hints"]) == 1


def test_t1_t3_mixed_pair_confidence_is_medium(patch_not_mapped):
    """T1+T3 pair uses the lower tier's confidence (medium from T3)."""
    dag = _dag({"a": _task("EmptyOperator"), "b": _task("CustomOp")})
    report = analyze_serialized_dag(dag, extra_clear_types={"CustomOp"})
    hints = report["clear_task_overlap_hints"]
    assert len(hints) == 1
    assert hints[0]["confidence"] == "medium"


def test_parse_extra_clear_types():
    result = _parse_extra_clear_types("Foo,Bar, Baz ,")
    assert result == frozenset({"Foo", "Bar", "Baz"})


def test_parse_extra_clear_types_empty():
    assert _parse_extra_clear_types("") == frozenset()
    assert _parse_extra_clear_types("  ,  ,") == frozenset()


# ---------------------------------------------------------------------------
# Allowlist metadata in report
# ---------------------------------------------------------------------------


def test_report_contains_tier_breakdown(patch_not_mapped):
    dag = _dag({"a": _task("EmptyOperator")})
    report = analyze_serialized_dag(dag, extra_clear_types={"CustomOp"})
    tiers = report["clear_operator_allowlist_tiers"]
    assert "EmptyOperator" in tiers["t1_trivial"]
    assert "PythonOperator" in tiers["t2_computation"]
    assert "CustomOp" in tiers["t3_user_extended"]


def test_report_clear_allowlist_is_union_of_all_tiers(patch_not_mapped):
    dag = _dag({"a": _task("EmptyOperator")})
    report = analyze_serialized_dag(dag, extra_clear_types={"MyOp"})
    allowlist = set(report["clear_operator_allowlist"])
    assert "EmptyOperator" in allowlist
    assert "PythonOperator" in allowlist
    assert "MyOp" in allowlist


def test_graph_metrics_include_tier_counts(patch_not_mapped):
    dag = _dag(
        {
            "e": _task("EmptyOperator"),
            "p": _task("PythonOperator"),
            "c": _task("CustomOp"),
            "o": _task("UnknownOp"),
        }
    )
    report = analyze_serialized_dag(dag, extra_clear_types={"CustomOp"})
    tc = report["graph_metrics"]["clear_tier_counts"]
    assert tc["t1_trivial"] == 1
    assert tc["t2_computation"] == 1
    assert tc["t3_user_extended"] == 1


def test_task_classifications_include_clear_tier(patch_not_mapped):
    dag = _dag({"a": _task("EmptyOperator"), "b": _task("PythonOperator"), "c": _task("UnknownOp")})
    report = analyze_serialized_dag(dag)
    by_id = {t["task_id"]: t for t in report["task_classifications"]}
    assert by_id["a"].get("clear_tier") == "t1_trivial"
    assert by_id["b"].get("clear_tier") == "t2_computation"
    assert "clear_tier" not in by_id["c"]


# ---------------------------------------------------------------------------
# Abstentions and opaque tasks
# ---------------------------------------------------------------------------


def test_abstention_is_reference_only_no_extra_fields(patch_not_mapped):
    """Abstentions are graph reference rows, not parallel recommendations."""
    dag = _dag(
        {
            "clear1": _task("EmptyOperator"),
            "opaque1": _task("UnknownOperator"),
        }
    )
    report = analyze_serialized_dag(dag)
    abst = report["abstained_parallel_hints"]
    assert len(abst) == 1
    assert "abstain_reason" in abst[0]
    assert "recommendation_key" not in abst[0]


def test_previously_opaque_bash_now_clear(patch_not_mapped):
    """BashOperator is now T2-clear, so a BashOperator pair generates hints (not abstentions)."""
    dag = _dag({"clear1": _task("EmptyOperator"), "bash1": _task("BashOperator")})
    report = analyze_serialized_dag(dag)
    # Both are clear now; should get a hint, not an abstention
    assert len(report["clear_task_overlap_hints"]) == 1
    assert len(report["abstained_parallel_hints"]) == 0


# ---------------------------------------------------------------------------
# Large DAG (pair analysis limit)
# ---------------------------------------------------------------------------


def test_analyze_skips_pair_enumeration_over_task_limit(patch_not_mapped):
    """Beyond the task limit we still classify but skip O(n²) pair analysis."""
    oversized = UAPE_FULL_PAIR_ANALYSIS_TASK_LIMIT + 1
    dag = SimpleNamespace(
        dag_id="big",
        task_dict={
            f"t{i}": SimpleNamespace(downstream_task_ids=[], task_type="BashOperator")
            for i in range(oversized)
        },
    )
    report = analyze_serialized_dag(dag)
    assert report["analysis_limits"]["full_independent_pair_analysis"] is False
    assert report["structurally_independent_pair_count"] == 0
    assert len(report["task_classifications"]) == oversized


# ---------------------------------------------------------------------------
# Mapped operators are always opaque
# ---------------------------------------------------------------------------


def test_mapped_operator_is_always_opaque():
    with patch("airflow.serialization.definitions.mappedoperator.is_mapped", return_value=True):
        dag = _dag({"a": _task("EmptyOperator"), "b": _task("EmptyOperator")})
        report = analyze_serialized_dag(dag)
        # All mapped → all opaque → no hints
        assert report["clear_task_overlap_hints"] == []
        for cls in report["task_classifications"]:
            assert cls["opacity"] == "opaque"
            assert "mapped operator" in cls["opacity_reason"]
