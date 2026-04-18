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
Conservative structural independence analysis for serialized DAGs.

The clear-operator allowlist is tiered:

* **Tier 1** (``t1_trivial``) — operators with no external I/O or side effects; confidence ``high``.
* **Tier 2** (``t2_computation``) — common Airflow operators whose parallelism is safe by design
  (Python callables, Bash, time-based sensors, control-flow operators); confidence ``medium_high``.
* **Tier 3** (``t3_user_extended``) — caller-supplied types via the ``UAPE_EXTRA_CLEAR_OPERATOR_TYPES``
  environment variable (comma-separated) or the ``extra_clear_types`` argument to
  ``analyze_serialized_dag``; confidence ``medium``.

Mapped operators are always opaque. Only parallel overlap hints are actionable suggestions; all other
report fields are diagnostics. Hints are advisory only.

Report fields are versioned via ``report_schema_version``; see ``analyze_serialized_dag`` return value.
"""

from __future__ import annotations

import importlib.metadata
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from airflow.serialization.definitions.dag import SerializedDAG

# ---------------------------------------------------------------------------
# Tiered clear-operator allowlists
# ---------------------------------------------------------------------------

# Tier 1: trivially no external side effects; structural independence is highly reliable.
_CLEAR_T1_TYPES: frozenset[str] = frozenset(
    {
        "EmptyOperator",
        "DummyOperator",  # legacy alias for EmptyOperator
        "LatestOnlyOperator",
    }
)

# Tier 2: common Airflow operators whose execution does not inherently share external resources
# with other operator instances of the same type. The advisory is still structural-graph-only.
_CLEAR_T2_TYPES: frozenset[str] = frozenset(
    {
        # Python-based computation
        "PythonOperator",
        "BranchPythonOperator",
        "ShortCircuitOperator",
        "PythonVirtualenvOperator",
        "ExternalPythonOperator",
        "PythonSensor",
        # Bash-based computation
        "BashOperator",
        "BashSensor",
        # Time-based sensors — only poll the clock, no external mutations
        "TimeSensor",
        "TimeDeltaSensor",
        "DateTimeSensor",
        # Control-flow / branching
        "BranchDateTimeOperator",
        "BranchDayOfWeekOperator",
        # DAG-level control
        "TriggerDagRunOperator",
        # Airflow-internal sensors (poll the metadata DB, no external writes)
        "ExternalTaskSensor",
        "ExternalTaskMarker",
    }
)

# Combined built-in allowlist (T1 ∪ T2). Exported for inspection.
UAPE_CLEAR_OPERATOR_TYPES: frozenset[str] = _CLEAR_T1_TYPES | _CLEAR_T2_TYPES

# ---------------------------------------------------------------------------
# Backward-compat alias: originally only EmptyOperator was in the allowlist.
# Keep the name so callers that imported it directly still work.
# ---------------------------------------------------------------------------
_CLEAR_OPERATOR_TYPES: frozenset[str] = UAPE_CLEAR_OPERATOR_TYPES

# ---------------------------------------------------------------------------
# Analysis limits
# ---------------------------------------------------------------------------

# Pairwise analysis is O(n²); beyond this we still emit classifications but skip pair enumeration.
UAPE_FULL_PAIR_ANALYSIS_TASK_LIMIT: int = 200

# Large DAGs can yield many abstentions; cap payload size while preserving totals.
UAPE_MAX_ABSTENTIONS_RETURNED: int = 2500

_REPORT_SCHEMA_VERSION: str = "1.2"
_POLICY_ID: str = "conservative_v2"

# Environment variable name for user-supplied extra clear types.
UAPE_EXTRA_CLEAR_TYPES_ENV: str = "UAPE_EXTRA_CLEAR_OPERATOR_TYPES"

# Map from operator type → (tier label, confidence label)
_TIER_CONFIDENCE: dict[str, tuple[str, str]] = {
    "t1_trivial": ("t1_trivial", "high"),
    "t2_computation": ("t2_computation", "medium_high"),
    "t3_user_extended": ("t3_user_extended", "medium"),
}


def _provider_distribution_version() -> str:
    try:
        return importlib.metadata.version("apache-airflow-providers-uape")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_extra_clear_types(raw: str) -> frozenset[str]:
    """Parse a comma-separated string of operator type names into a frozenset."""
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


def _env_extra_clear_types() -> frozenset[str]:
    raw = os.environ.get(UAPE_EXTRA_CLEAR_TYPES_ENV, "")
    return _parse_extra_clear_types(raw)


def _downstream_adjacency(task_dict: dict[str, Any]) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for tid, task in task_dict.items():
        downstream = getattr(task, "downstream_task_ids", None) or ()
        adj[tid] = set(downstream)
    return adj


def _downstream_reachable(start: str, adj: dict[str, set[str]]) -> set[str]:
    stack = [start]
    seen: set[str] = set()
    while stack:
        u = stack.pop()
        for v in adj.get(u, ()):
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def _classify_task(
    task: Any,
    user_types: frozenset[str],
) -> tuple[Literal["clear", "opaque"], str, str | None]:
    """
    Classify one task.

    Returns ``(opacity, reason, clear_tier)`` where *clear_tier* is one of
    ``"t1_trivial"``, ``"t2_computation"``, ``"t3_user_extended"``, or ``None``
    when the task is opaque.
    """
    from airflow.serialization.definitions.mappedoperator import is_mapped

    if is_mapped(task):
        return "opaque", "mapped operator (conservative default)", None
    task_type = getattr(task, "task_type", None) or "unknown"
    if task_type in _CLEAR_T1_TYPES:
        return (
            "clear",
            f"task_type {task_type!r} is on the T1 (trivial) built-in clear allowlist",
            "t1_trivial",
        )
    if task_type in _CLEAR_T2_TYPES:
        return (
            "clear",
            f"task_type {task_type!r} is on the T2 (computation) built-in clear allowlist",
            "t2_computation",
        )
    if task_type in user_types:
        return (
            "clear",
            f"task_type {task_type!r} is on the user-extended clear allowlist (T3)",
            "t3_user_extended",
        )
    return "opaque", f"task_type {task_type!r} is not on the clear allowlist (conservative default)", None


def _hint_confidence(tier_a: str | None, tier_b: str | None) -> str:
    """
    Derive the composite hint confidence from the two tasks' clear tiers.

    The hint confidence equals the lower tier's confidence so that a T1+T2 pair
    does not overstate certainty.
    """
    tier_order = ["t1_trivial", "t2_computation", "t3_user_extended"]
    tiers = [t for t in [tier_a, tier_b] if t is not None]
    if not tiers:
        return "low"
    worst = max((tier_order.index(t) for t in tiers), default=0)
    _, confidence = _TIER_CONFIDENCE.get(tier_order[worst], ("unknown", "low"))
    return confidence


def _graph_metrics(
    task_ids: list[str],
    adj: dict[str, set[str]],
    opacity: dict[str, tuple[Literal["clear", "opaque"], str, str | None]],
) -> dict[str, Any]:
    edge_count = sum(len(adj[tid]) for tid in task_ids)
    clear_n = sum(1 for tid in task_ids if opacity[tid][0] == "clear")
    tier_counts: dict[str, int] = {"t1_trivial": 0, "t2_computation": 0, "t3_user_extended": 0}
    for tid in task_ids:
        tier = opacity[tid][2]
        if tier is not None:
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
    return {
        "task_count": len(task_ids),
        "dependency_edge_count": edge_count,
        "clear_task_count": clear_n,
        "opaque_task_count": len(task_ids) - clear_n,
        "clear_tier_counts": tier_counts,
    }


def _executive_summary(
    *,
    task_count: int,
    hint_count: int,
    pair_analysis_skipped: bool,
) -> str:
    """One-line product summary: only parallel *opportunities* are highlighted here."""
    if pair_analysis_skipped:
        return (
            f"Parallel overlap scan skipped ({task_count} tasks over the analysis limit); "
            f"opacity labels for each task are still in the export."
        )
    if hint_count == 0:
        return (
            f"No parallel overlap opportunities: among {task_count} tasks, no pair is both on the "
            f"clear allowlist and structurally independent (see overview for opacity table)."
        )
    return (
        f"{hint_count} parallel overlap opportunity(ies): allowlisted clear task pair(s) with "
        f"no serialized dependency path between them (scheduler may run them together when ready)."
    )


def analyze_serialized_dag(
    dag: SerializedDAG,
    *,
    extra_clear_types: frozenset[str] | set[str] | None = None,
) -> dict[str, Any]:
    """
    Return a JSON-serializable report for one serialized DAG.

    Parameters
    ----------
    dag:
        A deserialized ``SerializedDAG`` object.
    extra_clear_types:
        Additional operator type names to treat as clear (T3).  When ``None``,
        the value is read from the ``UAPE_EXTRA_CLEAR_OPERATOR_TYPES`` environment
        variable (comma-separated).  Pass an empty set to suppress env-var lookup.
    """
    user_types: frozenset[str]
    if extra_clear_types is None:
        user_types = _env_extra_clear_types()
    else:
        user_types = frozenset(extra_clear_types)

    effective_clear = UAPE_CLEAR_OPERATOR_TYPES | user_types

    task_dict = dag.task_dict
    task_ids = sorted(task_dict)
    adj = _downstream_adjacency(task_dict)

    opacity: dict[str, tuple[Literal["clear", "opaque"], str, str | None]] = {}
    task_classifications: list[dict[str, Any]] = []
    for tid in task_ids:
        op, reason, tier = _classify_task(task_dict[tid], user_types)
        opacity[tid] = (op, reason, tier)
        entry: dict[str, Any] = {
            "task_id": tid,
            "task_type": getattr(task_dict[tid], "task_type", None),
            "opacity": op,
            "opacity_reason": reason,
        }
        if tier is not None:
            entry["clear_tier"] = tier
        task_classifications.append(entry)

    metrics = _graph_metrics(task_ids, adj, opacity)
    base_meta = {
        "report_schema_version": _REPORT_SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "uape_provider_version": _provider_distribution_version(),
        "dag_id": dag.dag_id,
        "policy": _POLICY_ID,
        "clear_operator_allowlist": sorted(effective_clear),
        "clear_operator_allowlist_tiers": {
            "t1_trivial": sorted(_CLEAR_T1_TYPES),
            "t2_computation": sorted(_CLEAR_T2_TYPES),
            "t3_user_extended": sorted(user_types),
        },
        "graph_metrics": metrics,
        "task_classifications": task_classifications,
    }

    if len(task_ids) > UAPE_FULL_PAIR_ANALYSIS_TASK_LIMIT:
        return {
            **base_meta,
            "analysis_limits": {
                "full_independent_pair_analysis": False,
                "reason": "task_count_exceeds_limit",
                "task_count": len(task_ids),
                "task_limit": UAPE_FULL_PAIR_ANALYSIS_TASK_LIMIT,
                "note": (
                    "Pairwise structural independence is skipped to bound CPU and memory; "
                    "per-task opacity classifications are still complete."
                ),
            },
            "structurally_independent_pairs": [],
            "structurally_independent_pair_count": 0,
            "clear_task_overlap_hints": [],
            "clear_task_overlap_hints_count": 0,
            "abstained_parallel_hints": [],
            "abstained_parallel_hints_total": 0,
            "abstained_parallel_hints_returned": 0,
            "executive_summary": _executive_summary(
                task_count=len(task_ids),
                hint_count=0,
                pair_analysis_skipped=True,
            ),
        }

    reach: dict[str, set[str]] = {tid: _downstream_reachable(tid, adj) for tid in task_ids}

    structural_pairs: list[dict[str, Any]] = []
    for i, a in enumerate(task_ids):
        for b in task_ids[i + 1 :]:
            if b in reach[a] or a in reach[b]:
                continue
            structural_pairs.append(
                {
                    "task_a": a,
                    "task_b": b,
                    "proof": {
                        "kind": "no_directed_path_either_direction",
                        "detail": (
                            f"no directed path from {a!r} to {b!r} and none from {b!r} to {a!r} "
                            "in the serialized dependency graph"
                        ),
                    },
                }
            )

    clear_overlap_hints: list[dict[str, Any]] = []
    abstained: list[dict[str, Any]] = []
    abstained_total = 0
    for pair in structural_pairs:
        a, b = pair["task_a"], pair["task_b"]
        oa, ra, tier_a = opacity[a]
        ob, rb, tier_b = opacity[b]
        if oa == "clear" and ob == "clear":
            confidence = _hint_confidence(tier_a, tier_b)
            clear_overlap_hints.append(
                {
                    **pair,
                    "task_a_clear_tier": tier_a,
                    "task_b_clear_tier": tier_b,
                    "recommendation": "advisory_parallel_overlap_by_declared_graph_only",
                    "recommendation_key": "graph_independent_clear_allowlist_pair",
                    "confidence": confidence,
                    "severity": "informational",
                    "recommendation_summary": (
                        f"{a} and {b} can run at the same time — neither depends on the other in this DAG."
                    ),
                    "caveat": (
                        "Based on DAG structure only. If these tasks share files, databases, "
                        "or other external resources, check with task owners before relying on "
                        "parallel execution."
                    ),
                    "suggested_next_steps": [
                        f"Verify with the owners of {a} and {b} that no hidden ordering is needed"
                        " (shared files, queues, external systems).",
                        "If conflicts appear at runtime, add an explicit dependency arrow between the tasks in your DAG.",
                    ],
                }
            )
        else:
            abstained_total += 1
            reasons: list[str] = []
            if oa != "clear":
                reasons.append(f"{a}: {ra}")
            if ob != "clear":
                reasons.append(f"{b}: {rb}")
            row = {
                **pair,
                "abstain_reason": "; ".join(reasons),
            }
            if len(abstained) < UAPE_MAX_ABSTENTIONS_RETURNED:
                abstained.append(row)

    abstentions_truncated = abstained_total > len(abstained)

    return {
        **base_meta,
        "analysis_limits": {
            "full_independent_pair_analysis": True,
            "task_count": len(task_ids),
            "task_limit": UAPE_FULL_PAIR_ANALYSIS_TASK_LIMIT,
            "abstentions_capped": abstentions_truncated,
            "abstentions_cap": UAPE_MAX_ABSTENTIONS_RETURNED,
        },
        "structurally_independent_pairs": structural_pairs,
        "structurally_independent_pair_count": len(structural_pairs),
        "clear_task_overlap_hints": clear_overlap_hints,
        "clear_task_overlap_hints_count": len(clear_overlap_hints),
        "abstained_parallel_hints": abstained,
        "abstained_parallel_hints_total": abstained_total,
        "abstained_parallel_hints_returned": len(abstained),
        "executive_summary": _executive_summary(
            task_count=len(task_ids),
            hint_count=len(clear_overlap_hints),
            pair_analysis_skipped=False,
        ),
    }


__all__ = [
    "UAPE_CLEAR_OPERATOR_TYPES",
    "UAPE_EXTRA_CLEAR_TYPES_ENV",
    "UAPE_FULL_PAIR_ANALYSIS_TASK_LIMIT",
    "UAPE_MAX_ABSTENTIONS_RETURNED",
    "analyze_serialized_dag",
]
