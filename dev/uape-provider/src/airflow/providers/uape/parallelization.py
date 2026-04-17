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

Default: every operator is *opaque* except a tiny built-in allowlist (``EmptyOperator`` only).
Mapped operators are always opaque. Recommendations are advisory only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from airflow.serialization.definitions.dag import SerializedDAG

# Built-in types treated as having a bounded contract for *reporting* sibling overlap only.
_CLEAR_OPERATOR_TYPES: frozenset[str] = frozenset({"EmptyOperator"})


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


def _classify_task(task: Any) -> tuple[Literal["clear", "opaque"], str]:
    from airflow.serialization.definitions.mappedoperator import is_mapped

    if is_mapped(task):
        return "opaque", "mapped operator (conservative default)"
    task_type = getattr(task, "task_type", None) or "unknown"
    if task_type in _CLEAR_OPERATOR_TYPES:
        return "clear", f"task_type {task_type!r} is on the built-in clear allowlist"
    return "opaque", f"task_type {task_type!r} is not on the clear allowlist (conservative default)"


def analyze_serialized_dag(dag: SerializedDAG) -> dict[str, Any]:
    """Return a JSON-serializable report for one serialized DAG."""
    task_dict = dag.task_dict
    adj = _downstream_adjacency(task_dict)
    task_ids = sorted(task_dict)
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

    task_classifications: list[dict[str, Any]] = []
    opacity: dict[str, tuple[Literal["clear", "opaque"], str]] = {}
    for tid in task_ids:
        op, reason = _classify_task(task_dict[tid])
        opacity[tid] = (op, reason)
        task_classifications.append(
            {
                "task_id": tid,
                "task_type": getattr(task_dict[tid], "task_type", None),
                "opacity": op,
                "opacity_reason": reason,
            }
        )

    clear_overlap_hints: list[dict[str, Any]] = []
    abstained: list[dict[str, Any]] = []
    for pair in structural_pairs:
        a, b = pair["task_a"], pair["task_b"]
        oa, ra = opacity[a]
        ob, rb = opacity[b]
        if oa == "clear" and ob == "clear":
            clear_overlap_hints.append(
                {
                    **pair,
                    "recommendation": "advisory_parallel_overlap_by_declared_graph_only",
                    "caveat": (
                        "Hidden dependencies are out of scope; confirm before changing task dependencies."
                    ),
                }
            )
        else:
            reasons: list[str] = []
            if oa != "clear":
                reasons.append(f"{a}: {ra}")
            if ob != "clear":
                reasons.append(f"{b}: {rb}")
            abstained.append({**pair, "abstain_reason": "; ".join(reasons)})

    return {
        "dag_id": dag.dag_id,
        "policy": "conservative_v1",
        "clear_operator_allowlist": sorted(_CLEAR_OPERATOR_TYPES),
        "task_classifications": task_classifications,
        "structurally_independent_pairs": structural_pairs,
        "clear_task_overlap_hints": clear_overlap_hints,
        "abstained_parallel_hints": abstained,
    }
