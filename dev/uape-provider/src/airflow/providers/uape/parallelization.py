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
Uncertainty-Aware Parallelization Engine (UAPE) — v2

Analyzes *declared* DAG edges for potential false dependencies using four independent signals:

  Signal 1 — Asset overlap        (weight 35 %): declared inlets/outlets share a URI prefix
  Signal 2 — XCom code analysis   (weight 25 %): AST scan for xcom_pull referencing the upstream task
  Signal 3 — Timing correlation   (weight 20 %): historical start/end gap between the two tasks
  Signal 4 — Transitive reduction (weight 20 %): edge removed by transitive reduction → redundant

Confidence score = Σ(signal_weight × passed) / Σ(available_signal_weights) × 100

Thresholds:
  score < 40   → verdict "remove"   (likely false dependency)
  40 ≤ score < 65 → "uncertain"     (manual review recommended)
  score ≥ 65   → "keep"             (real dependency detected)

Monte Carlo simulation over historical task durations estimates P50 / P95 time savings when
an edge is recommended for removal.

Skipped signals (unavailable data, missing libs) are excluded from the denominator so they do
not penalise real dependencies.
"""

from __future__ import annotations

import ast
import importlib.metadata
import inspect
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

SIGNAL_WEIGHT_ASSET_OVERLAP: int = 35
SIGNAL_WEIGHT_XCOM_ANALYSIS: int = 25
SIGNAL_WEIGHT_TIMING_CORR: int = 20
SIGNAL_WEIGHT_TRANSITIVE: int = 20

THRESHOLD_REMOVE: int = 40  # score < 40  → remove
THRESHOLD_UNCERTAIN: int = 65  # 40 ≤ score < 65 → uncertain  /  ≥ 65 → keep

N_SIMULATIONS: int = 10_000
MIN_TIMING_RUNS: int = 10
MIN_PROFILE_RUNS: int = 5

_REPORT_SCHEMA_VERSION: str = "2.0"
_POLICY_ID: str = "uncertainty_aware_v1"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SignalResult:
    """Result from one dependency-inference signal for a single edge."""

    name: str
    passed: bool
    weight: int
    explanation: str
    skipped: bool = False  # True when the signal could not run — excluded from scoring

    @property
    def effective_weight(self) -> int:
        return 0 if self.skipped else self.weight

    @property
    def score_contribution(self) -> int:
        return self.weight if (self.passed and not self.skipped) else 0


@dataclass
class EdgeScore:
    from_task: str
    to_task: str
    signals: list[SignalResult]
    confidence_score: int
    verdict: str  # 'keep' | 'uncertain' | 'remove'


@dataclass
class DurationProfile:
    task_id: str
    dist_name: str
    params: tuple  # scipy distribution parameters (empty tuple when empirical)
    mean: float
    std: float
    p5: float
    p50: float
    p95: float
    n_samples: int


@dataclass
class SimulationResult:
    mean_savings_seconds: float
    p5_savings_seconds: float
    p50_savings_seconds: float
    p95_savings_seconds: float
    prob_improvement: float
    n_simulations: int


# ---------------------------------------------------------------------------
# Internal graph helpers
# ---------------------------------------------------------------------------


def _build_adjacency(task_dict: dict[str, Any]) -> dict[str, set[str]]:
    return {tid: set(getattr(task, "downstream_task_ids", None) or ()) for tid, task in task_dict.items()}


def _topological_order(task_ids: list[str], adj: dict[str, set[str]]) -> list[str]:
    """Return task_ids in topological (dependency) order using iterative DFS."""
    visited: set[str] = set()
    order: list[str] = []

    def _visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for nxt in adj.get(node, ()):
            _visit(nxt)
        order.append(node)

    for tid in task_ids:
        _visit(tid)
    order.reverse()
    return order


def _predecessors_map(task_ids: list[str], adj: dict[str, set[str]]) -> dict[str, list[str]]:
    preds: dict[str, list[str]] = defaultdict(list)
    for tid in task_ids:
        for s in adj.get(tid, ()):
            preds[s].append(tid)
    return dict(preds)


def _provider_version() -> str:
    try:
        return importlib.metadata.version("apache-airflow-providers-uape")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Signal 1: Asset / Dataset URI overlap
# ---------------------------------------------------------------------------


def _get_asset_uris(task: Any, attr: str) -> set[str]:
    """Extract normalised URIs from a task's inlets or outlets list."""
    items = getattr(task, attr, None) or []
    uris: set[str] = set()
    for item in items:
        uri = getattr(item, "uri", None)
        if uri:
            uris.add(str(uri).rstrip("/"))
        elif isinstance(item, str):
            uris.add(item.rstrip("/"))
    return uris


def _paths_overlap(outlets: set[str], inlets: set[str]) -> set[str]:
    """Return outlets that prefix-match any inlet (covers bucket-prefix patterns)."""
    matched: set[str] = set()
    for w in outlets:
        for r in inlets:
            if r.startswith(w) or w.startswith(r):
                matched.add(w)
    return matched


def signal_asset_overlap(task_dict: dict[str, Any], upstream_id: str, downstream_id: str) -> SignalResult:
    """Signal 1 — checks declared inlets/outlets for shared asset URIs."""
    upstream = task_dict.get(upstream_id)
    downstream = task_dict.get(downstream_id)
    if upstream is None or downstream is None:
        return SignalResult(
            name="asset_overlap",
            passed=False,
            weight=SIGNAL_WEIGHT_ASSET_OVERLAP,
            skipped=True,
            explanation="Task not found in task_dict",
        )

    up_outlets = _get_asset_uris(upstream, "outlets")
    down_inlets = _get_asset_uris(downstream, "inlets")

    if not up_outlets and not down_inlets:
        return SignalResult(
            name="asset_overlap",
            passed=False,
            weight=SIGNAL_WEIGHT_ASSET_OVERLAP,
            explanation="Neither task declares asset inlets/outlets — no overlap possible",
        )

    overlap = _paths_overlap(up_outlets, down_inlets)
    if overlap:
        return SignalResult(
            name="asset_overlap",
            passed=True,
            weight=SIGNAL_WEIGHT_ASSET_OVERLAP,
            explanation=f"Shared asset URIs: {sorted(overlap)}",
        )

    return SignalResult(
        name="asset_overlap",
        passed=False,
        weight=SIGNAL_WEIGHT_ASSET_OVERLAP,
        explanation=(
            f"{upstream_id} outlets={sorted(up_outlets) or 'none'}; "
            f"{downstream_id} inlets={sorted(down_inlets) or 'none'} — no overlap"
        ),
    )


# ---------------------------------------------------------------------------
# Signal 2: XCom code analysis
# ---------------------------------------------------------------------------


class _XComPullVisitor(ast.NodeVisitor):
    def __init__(self, upstream_id: str) -> None:
        self.upstream_id = upstream_id
        self.found = False

    def visit_Call(self, node: ast.Call) -> None:
        is_xcom_pull = isinstance(node.func, ast.Attribute) and node.func.attr == "xcom_pull"
        if is_xcom_pull:
            for kw in node.keywords:
                if kw.arg == "task_ids":
                    val = kw.value
                    if isinstance(val, ast.Constant) and val.value == self.upstream_id:
                        self.found = True
                    elif isinstance(val, ast.List):
                        for elt in val.elts:
                            if isinstance(elt, ast.Constant) and elt.value == self.upstream_id:
                                self.found = True
        self.generic_visit(node)


def signal_xcom_analysis(task_dict: dict[str, Any], upstream_id: str, downstream_id: str) -> SignalResult:
    """Signal 2 — AST-parses the downstream python_callable for xcom_pull referencing upstream."""
    task = task_dict.get(downstream_id)
    if task is None:
        return SignalResult(
            name="xcom_analysis",
            passed=False,
            weight=SIGNAL_WEIGHT_XCOM_ANALYSIS,
            skipped=True,
            explanation="Downstream task not found",
        )

    callable_ = getattr(task, "python_callable", None)
    if callable_ is None:
        return SignalResult(
            name="xcom_analysis",
            passed=False,
            weight=SIGNAL_WEIGHT_XCOM_ANALYSIS,
            skipped=True,
            explanation=(f"Task {downstream_id!r} has no python_callable — skipping XCom code analysis"),
        )

    try:
        source = inspect.getsource(callable_)
        tree = ast.parse(source)
    except (OSError, TypeError, IndentationError, SyntaxError):
        return SignalResult(
            name="xcom_analysis",
            passed=False,
            weight=SIGNAL_WEIGHT_XCOM_ANALYSIS,
            skipped=True,
            explanation=f"Could not parse source of {downstream_id!r} — skipping",
        )

    visitor = _XComPullVisitor(upstream_id)
    visitor.visit(tree)

    if visitor.found:
        return SignalResult(
            name="xcom_analysis",
            passed=True,
            weight=SIGNAL_WEIGHT_XCOM_ANALYSIS,
            explanation=f"{downstream_id} calls xcom_pull(task_ids={upstream_id!r})",
        )
    return SignalResult(
        name="xcom_analysis",
        passed=False,
        weight=SIGNAL_WEIGHT_XCOM_ANALYSIS,
        explanation=f"No xcom_pull referencing {upstream_id!r} found in {downstream_id!r}",
    )


# ---------------------------------------------------------------------------
# Signal 3: Timing correlation
# ---------------------------------------------------------------------------


def signal_timing_correlation(
    dag_id: str,
    upstream_id: str,
    downstream_id: str,
    session: Session | None,
) -> SignalResult:
    """Signal 3 — checks whether downstream consistently starts shortly after upstream ends."""
    if session is None:
        return SignalResult(
            name="timing_correlation",
            passed=False,
            weight=SIGNAL_WEIGHT_TIMING_CORR,
            skipped=True,
            explanation="No DB session — skipping timing analysis",
        )

    try:
        import numpy as np
    except ImportError:
        return SignalResult(
            name="timing_correlation",
            passed=False,
            weight=SIGNAL_WEIGHT_TIMING_CORR,
            skipped=True,
            explanation="numpy not installed — skipping timing analysis",
        )

    try:
        from sqlalchemy import select

        from airflow.models import TaskInstance

        stmt = (
            select(TaskInstance)
            .where(
                TaskInstance.dag_id == dag_id,
                TaskInstance.task_id.in_([upstream_id, downstream_id]),
                TaskInstance.state == "success",
            )
            .order_by(TaskInstance.run_id)
        )
        tis = list(session.execute(stmt).scalars())
    except Exception as exc:
        return SignalResult(
            name="timing_correlation",
            passed=False,
            weight=SIGNAL_WEIGHT_TIMING_CORR,
            skipped=True,
            explanation=f"DB query failed: {exc}",
        )

    by_run: dict[str, dict[str, Any]] = defaultdict(dict)
    for ti in tis:
        by_run[ti.run_id][ti.task_id] = ti

    gaps: list[float] = []
    for tasks in by_run.values():
        if upstream_id in tasks and downstream_id in tasks:
            up_end = getattr(tasks[upstream_id], "end_date", None)
            down_start = getattr(tasks[downstream_id], "start_date", None)
            if up_end is not None and down_start is not None:
                gaps.append((down_start - up_end).total_seconds())

    if len(gaps) < MIN_TIMING_RUNS:
        return SignalResult(
            name="timing_correlation",
            passed=False,
            weight=SIGNAL_WEIGHT_TIMING_CORR,
            explanation=(f"Only {len(gaps)} usable run(s) — need {MIN_TIMING_RUNS}+ for timing analysis"),
        )

    arr = np.array(gaps)
    mean_gap = float(arr.mean())
    std_gap = float(arr.std())
    # Low mean gap (<10 s) AND low variance indicates the downstream task
    # consistently starts immediately after the upstream — strong coupling signal.
    tightly_coupled = mean_gap < 10.0 and std_gap < 5.0

    return SignalResult(
        name="timing_correlation",
        passed=tightly_coupled,
        weight=SIGNAL_WEIGHT_TIMING_CORR,
        explanation=(
            f"Mean gap: {mean_gap:.1f}s, std: {std_gap:.1f}s — "
            f"{'tightly coupled (likely dependent)' if tightly_coupled else 'loose coupling (likely independent)'}"
        ),
    )


# ---------------------------------------------------------------------------
# Signal 4: Transitive reduction
# ---------------------------------------------------------------------------


def signal_transitive_reduction(
    adj: dict[str, set[str]], upstream_id: str, downstream_id: str
) -> SignalResult:
    """Signal 4 — edge removed by transitive reduction means a longer path already covers it."""
    try:
        import networkx as nx
    except ImportError:
        return SignalResult(
            name="transitive_reduction",
            passed=False,
            weight=SIGNAL_WEIGHT_TRANSITIVE,
            skipped=True,
            explanation="networkx not installed — skipping transitive reduction check",
        )

    G: nx.DiGraph = nx.DiGraph()
    for node, succs in adj.items():
        G.add_node(node)
        for s in succs:
            G.add_edge(node, s)

    G_reduced = nx.transitive_reduction(G)
    if G_reduced.has_edge(upstream_id, downstream_id):
        return SignalResult(
            name="transitive_reduction",
            passed=True,
            weight=SIGNAL_WEIGHT_TRANSITIVE,
            explanation="Edge survives transitive reduction — it is a minimal direct dependency",
        )

    try:
        paths = list(nx.all_simple_paths(G, upstream_id, downstream_id))
        bypass = min((p for p in paths if len(p) > 2), key=len, default=None)
        bypass_str = " → ".join(bypass) if bypass else "(longer path exists)"
    except Exception:
        bypass_str = "(path computation failed)"

    return SignalResult(
        name="transitive_reduction",
        passed=False,
        weight=SIGNAL_WEIGHT_TRANSITIVE,
        explanation=f"Edge is redundant — path {bypass_str} already covers this dependency",
    )


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


def score_edge(from_task: str, to_task: str, signals: list[SignalResult]) -> EdgeScore:
    """Combine signal results into a normalised 0–100 confidence score."""
    total_weight = sum(s.effective_weight for s in signals)
    earned = sum(s.score_contribution for s in signals)

    if total_weight > 0:
        score = int(round(earned * 100 / total_weight))
    else:
        score = 50  # All signals skipped — no evidence either way

    if score < THRESHOLD_REMOVE:
        verdict = "remove"
    elif score < THRESHOLD_UNCERTAIN:
        verdict = "uncertain"
    else:
        verdict = "keep"

    return EdgeScore(
        from_task=from_task,
        to_task=to_task,
        signals=signals,
        confidence_score=score,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Task duration profiler
# ---------------------------------------------------------------------------


def _load_historical_durations(
    dag_id: str,
    task_id: str,
    session: Session,
    last_n: int = 200,
) -> list[float]:
    try:
        from sqlalchemy import select

        from airflow.models import TaskInstance

        stmt = (
            select(TaskInstance.duration)
            .where(
                TaskInstance.dag_id == dag_id,
                TaskInstance.task_id == task_id,
                TaskInstance.state == "success",
                TaskInstance.duration.isnot(None),
            )
            .order_by(TaskInstance.start_date.desc())
            .limit(last_n)
        )
        rows = list(session.execute(stmt).scalars())
        return [float(d) for d in rows if d and float(d) > 0]
    except Exception:
        return []


def fit_duration_profile(task_id: str, durations: list[float]) -> DurationProfile | None:
    """Fit the best statistical distribution to historical task durations."""
    if len(durations) < MIN_PROFILE_RUNS:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    arr = np.array(durations)

    try:
        from scipy import stats

        candidates = ["lognorm", "gamma", "norm"]
        best_dist_name: str | None = None
        best_params: tuple = ()
        best_aic = math.inf

        for dist_name in candidates:
            try:
                dist = getattr(stats, dist_name)
                params = dist.fit(arr)
                log_lik = float(dist.logpdf(arr, *params).sum())
                aic = 2 * len(params) - 2 * log_lik
                if aic < best_aic:
                    best_aic, best_dist_name, best_params = aic, dist_name, params
            except Exception:
                continue

        if best_dist_name is None:
            raise ImportError("no distribution converged")

        dist_obj = getattr(stats, best_dist_name)(*best_params)
        return DurationProfile(
            task_id=task_id,
            dist_name=best_dist_name,
            params=best_params,
            mean=float(dist_obj.mean()),
            std=float(dist_obj.std()),
            p5=float(dist_obj.ppf(0.05)),
            p50=float(dist_obj.ppf(0.50)),
            p95=float(dist_obj.ppf(0.95)),
            n_samples=len(durations),
        )

    except ImportError:
        # Empirical fallback without scipy
        return DurationProfile(
            task_id=task_id,
            dist_name="empirical",
            params=(),
            mean=float(np.mean(arr)),
            std=float(np.std(arr)),
            p5=float(np.percentile(arr, 5)),
            p50=float(np.percentile(arr, 50)),
            p95=float(np.percentile(arr, 95)),
            n_samples=len(durations),
        )


# ---------------------------------------------------------------------------
# Monte Carlo makespan simulation
# ---------------------------------------------------------------------------


def _vectorized_makespan(
    topo_order: list[str],
    preds: dict[str, list[str]],
    samples: dict[str, Any],  # {task_id: np.ndarray shape (n,)}
    n: int,
) -> Any:  # np.ndarray shape (n,)
    """Compute makespan for n simulations simultaneously using numpy broadcasting."""
    import numpy as np

    finish: dict[str, Any] = {}
    zeros = np.zeros(n)

    for tid in topo_order:
        pred_list = preds.get(tid, [])
        if pred_list:
            start = finish[pred_list[0]].copy()
            for p in pred_list[1:]:
                np.maximum(start, finish[p], out=start)
        else:
            start = zeros.copy()
        finish[tid] = start + samples.get(tid, zeros)

    if not finish:
        return zeros
    return max(finish.values(), key=lambda x: x.mean())


def simulate_savings(
    task_ids: list[str],
    adj_current: dict[str, set[str]],
    adj_proposed: dict[str, set[str]],
    profiles: dict[str, DurationProfile],
    n: int = N_SIMULATIONS,
) -> SimulationResult | None:
    """Run Monte Carlo to estimate time savings from the proposed edge removal."""
    if not profiles:
        return None

    try:
        import numpy as np
    except ImportError:
        return None

    rng = np.random.default_rng()

    def _sample_all(profiles_dict: dict[str, DurationProfile]) -> dict[str, Any]:
        """Sample durations for all tasks for n simulations."""
        out: dict[str, Any] = {}
        for tid, p in profiles_dict.items():
            if p.dist_name != "empirical" and p.params:
                try:
                    from scipy import stats

                    dist = getattr(stats, p.dist_name, None)
                    if dist is not None:
                        out[tid] = np.maximum(0.0, dist.rvs(*p.params, size=n))
                        continue
                except ImportError:
                    pass
            # Normal approximation fallback
            out[tid] = np.maximum(0.0, rng.normal(p.mean, max(p.std, 1e-9), size=n))
        return out

    samples = _sample_all(profiles)

    topo_current = _topological_order(task_ids, adj_current)
    preds_current = _predecessors_map(task_ids, adj_current)
    current_makespans = _vectorized_makespan(topo_current, preds_current, samples, n)

    topo_proposed = _topological_order(task_ids, adj_proposed)
    preds_proposed = _predecessors_map(task_ids, adj_proposed)
    proposed_makespans = _vectorized_makespan(topo_proposed, preds_proposed, samples, n)

    savings = current_makespans - proposed_makespans
    positive = savings[savings > 0]

    return SimulationResult(
        mean_savings_seconds=float(savings.mean()),
        p5_savings_seconds=float(np.percentile(savings, 5)),
        p50_savings_seconds=float(np.percentile(savings, 50)),
        p95_savings_seconds=float(np.percentile(savings, 95)),
        prob_improvement=float(len(positive) / n),
        n_simulations=n,
    )


# ---------------------------------------------------------------------------
# Recommendation builder
# ---------------------------------------------------------------------------


def _build_explanation(edge_score: EdgeScore) -> str:
    from_t, to_t = edge_score.from_task, edge_score.to_task
    score = edge_score.confidence_score

    if edge_score.verdict == "remove":
        failed = [s for s in edge_score.signals if not s.passed and not s.skipped]
        n_available = sum(1 for s in edge_score.signals if not s.skipped)
        return (
            f"Recommend removing {from_t} → {to_t} (confidence score: {score}/100). "
            f"{len(failed)} of {n_available} active signals found no evidence of a real dependency. "
            + " ".join(f"[{s.name}: {s.explanation}]" for s in failed)
        )
    if edge_score.verdict == "uncertain":
        return (
            f"Uncertain about {from_t} → {to_t} (score: {score}/100). "
            f"Mixed signals — manual review recommended."
        )
    passed = [s for s in edge_score.signals if s.passed and not s.skipped]
    return (
        f"Edge {from_t} → {to_t} appears to be a real dependency (score: {score}/100). "
        f"{len(passed)} signal(s) confirm it."
    )


def _find_suggested_fix(
    task_dict: dict[str, Any],
    to_task: str,
    from_task: str,
) -> str | None:
    """Suggest the actual upstream parent of to_task using asset URI overlap."""
    to_inlets = _get_asset_uris(task_dict.get(to_task) or {}, "inlets")
    if not to_inlets:
        return (
            f"Remove the explicit {from_task} → {to_task} dependency if {to_task} has "
            f"no data requirement on {from_task}'s output."
        )

    real_parents = [
        tid
        for tid, task in task_dict.items()
        if tid not in (from_task, to_task) and _paths_overlap(_get_asset_uris(task, "outlets"), to_inlets)
    ]

    if real_parents:
        parents_str = ", ".join(sorted(real_parents))
        return (
            f"Wire {to_task} after {parents_str} instead of {from_task}. "
            f"This allows {to_task} to start in parallel with tasks that also depend "
            f"only on {parents_str}."
        )
    return (
        f"Remove the explicit {from_task} → {to_task} edge. "
        f"If {to_task} has no data requirement on {from_task}'s output it can start "
        f"as soon as its other real dependencies complete."
    )


def _edge_to_dict(
    edge_score: EdgeScore,
    *,
    sim: SimulationResult | None = None,
    suggested_fix: str | None = None,
) -> dict[str, Any]:
    time_savings: dict[str, Any] | None = None
    if sim is not None:
        time_savings = {
            "mean_savings_seconds": round(sim.mean_savings_seconds, 1),
            "p5_savings_seconds": round(sim.p5_savings_seconds, 1),
            "p50_savings_seconds": round(sim.p50_savings_seconds, 1),
            "p95_savings_seconds": round(sim.p95_savings_seconds, 1),
            "prob_improvement": round(sim.prob_improvement, 3),
            "n_simulations": sim.n_simulations,
        }

    dag_diff: str | None = None
    if edge_score.verdict == "remove":
        fix_note = suggested_fix or f"Remove the explicit {edge_score.from_task} → {edge_score.to_task} edge"
        dag_diff = (
            f"# Suggested change:\n"
            f"# Remove: {edge_score.from_task} >> {edge_score.to_task}\n"
            f"# Apply:  {fix_note}\n\n"
            f"# Before:\n"
            f"# {edge_score.from_task} >> {edge_score.to_task}\n\n"
            f"# After (verify real parent first):\n"
            f"# [wire {edge_score.to_task} to its actual upstream dependency]\n"
        )

    return {
        "from_task": edge_score.from_task,
        "to_task": edge_score.to_task,
        "confidence_score": edge_score.confidence_score,
        "verdict": edge_score.verdict,
        "signals": [
            {
                "name": s.name,
                "passed": s.passed,
                "weight": s.weight,
                "skipped": s.skipped,
                "score_contribution": s.score_contribution,
                "explanation": s.explanation,
            }
            for s in edge_score.signals
        ],
        "plain_explanation": _build_explanation(edge_score),
        "suggested_fix": suggested_fix,
        "time_savings": time_savings,
        "dag_diff": dag_diff,
    }


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------


def analyze_dag_edges(
    dag: Any,
    *,
    session: Session | None = None,
    n_simulations: int = N_SIMULATIONS,
) -> dict[str, Any]:
    """
    Analyze all declared edges in a serialized DAG for potential false dependencies.

    Parameters
    ----------
    dag:
        A deserialized SerializedDAG object.
    session:
        SQLAlchemy session for querying historical TaskInstance data (timing correlation
        and duration profiling).  When ``None``, those signals are skipped.
    n_simulations:
        Monte Carlo iterations for time-savings estimation (default 10 000).
    """
    dag_id: str = dag.dag_id
    task_dict: dict[str, Any] = dag.task_dict
    task_ids = sorted(task_dict)
    adj = _build_adjacency(task_dict)

    all_edges = [(tid, s) for tid in task_ids for s in sorted(adj.get(tid, ()))]

    # Detect redundant edges via transitive reduction
    redundant_edges: list[tuple[str, str]] = []
    try:
        import networkx as nx

        G: nx.DiGraph = nx.DiGraph()
        G.add_nodes_from(task_ids)
        G.add_edges_from(all_edges)
        G_reduced = nx.transitive_reduction(G)
        redundant_edges = sorted(set(G.edges()) - set(G_reduced.edges()))
    except ImportError:
        pass

    # Load duration profiles when a session is available
    profiles: dict[str, DurationProfile] = {}
    if session is not None:
        for tid in task_ids:
            durations = _load_historical_durations(dag_id, tid, session)
            profile = fit_duration_profile(tid, durations)
            if profile is not None:
                profiles[tid] = profile

    # Analyse each declared edge
    edge_analyses: list[dict[str, Any]] = []
    for from_task, to_task in all_edges:
        signals = [
            signal_asset_overlap(task_dict, from_task, to_task),
            signal_xcom_analysis(task_dict, from_task, to_task),
            signal_timing_correlation(dag_id, from_task, to_task, session),
            signal_transitive_reduction(adj, from_task, to_task),
        ]

        edge_score = score_edge(from_task, to_task, signals)
        sim: SimulationResult | None = None
        suggested_fix: str | None = None

        if edge_score.verdict == "remove":
            suggested_fix = _find_suggested_fix(task_dict, to_task, from_task)
            if profiles:
                adj_proposed = {tid: set(succs) for tid, succs in adj.items()}
                adj_proposed[from_task].discard(to_task)
                sim = simulate_savings(task_ids, adj, adj_proposed, profiles, n=n_simulations)

        edge_analyses.append(_edge_to_dict(edge_score, sim=sim, suggested_fix=suggested_fix))

    remove_count = sum(1 for e in edge_analyses if e["verdict"] == "remove")
    uncertain_count = sum(1 for e in edge_analyses if e["verdict"] == "uncertain")
    keep_count = sum(1 for e in edge_analyses if e["verdict"] == "keep")

    return {
        "report_schema_version": _REPORT_SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "uape_provider_version": _provider_version(),
        "dag_id": dag_id,
        "policy": _POLICY_ID,
        "graph_metrics": {
            "task_count": len(task_ids),
            "dependency_edge_count": len(all_edges),
            "redundant_edge_count": len(redundant_edges),
        },
        "redundant_edges": [{"from_task": u, "to_task": v} for u, v in redundant_edges],
        "summary": {
            "total_edges": len(all_edges),
            "remove_count": remove_count,
            "uncertain_count": uncertain_count,
            "keep_count": keep_count,
            "has_historical_data": bool(profiles),
            "profiled_task_count": len(profiles),
        },
        "edge_analyses": edge_analyses,
        "executive_summary": _executive_summary(dag_id, len(all_edges), remove_count, uncertain_count),
    }


def _executive_summary(dag_id: str, total_edges: int, remove_count: int, uncertain_count: int) -> str:
    if total_edges == 0:
        return f"DAG {dag_id!r} has no declared edges to analyse."
    parts = []
    if remove_count:
        parts.append(f"{remove_count} edge(s) recommended for removal (likely false dependencies)")
    if uncertain_count:
        parts.append(f"{uncertain_count} edge(s) flagged for manual review")
    if not parts:
        return f"All {total_edges} edge(s) in {dag_id!r} appear to be real dependencies."
    return f"DAG {dag_id!r}: {'; '.join(parts)} out of {total_edges} total edge(s)."


__all__ = [
    "THRESHOLD_REMOVE",
    "THRESHOLD_UNCERTAIN",
    "N_SIMULATIONS",
    "SignalResult",
    "EdgeScore",
    "DurationProfile",
    "SimulationResult",
    "signal_asset_overlap",
    "signal_xcom_analysis",
    "signal_timing_correlation",
    "signal_transitive_reduction",
    "score_edge",
    "fit_duration_profile",
    "simulate_savings",
    "analyze_dag_edges",
]
