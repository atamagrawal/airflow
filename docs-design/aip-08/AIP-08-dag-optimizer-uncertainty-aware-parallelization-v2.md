# Uncertainty-Aware Parallelization Recommendation Engine for Apache Airflow

**Document version:** 1.0  
**Status:** Draft  
**Audience:** Platform engineers, data engineers, MLOps teams

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Goals and Non-Goals](#3-goals-and-non-goals)
4. [System Architecture](#4-system-architecture)
5. [Core Components](#5-core-components)
   - 5.1 [Dependency Inference Layer](#51-dependency-inference-layer)
   - 5.2 [Topological Level Scheduler](#52-topological-level-scheduler)
   - 5.3 [Transitive Reduction Module](#53-transitive-reduction-module)
   - 5.4 [Task Duration Profiler](#54-task-duration-profiler)
   - 5.5 [Monte Carlo Simulator](#55-monte-carlo-simulator)
   - 5.6 [Confidence Scoring Engine](#56-confidence-scoring-engine)
   - 5.7 [Recommendation Generator](#57-recommendation-generator)
   - 5.8 [DAG Rewriter](#58-dag-rewriter)
   - 5.9 [Airflow Plugin and UI Integration](#59-airflow-plugin-and-ui-integration)
6. [Data Models](#6-data-models)
7. [Algorithm Details](#7-algorithm-details)
8. [Uncertainty Modeling](#8-uncertainty-modeling)
9. [API Design](#9-api-design)
10. [Testing Strategy](#10-testing-strategy)
11. [Deployment and Operations](#11-deployment-and-operations)
12. [Limitations and Future Work](#12-limitations-and-future-work)

---

## 1. Executive Summary

Users of Apache Airflow define DAGs manually. In practice, they tend to write tasks top-to-bottom, serializing steps that could safely run in parallel. This wastes compute resources and increases total pipeline duration — often by 30–60% compared to an optimally parallelized DAG.

This document describes the design of an **Uncertainty-Aware Parallelization Recommendation Engine** — a system that:

- Analyzes user-defined DAGs using multiple independent signals (dataset lineage, static code analysis, historical timing, graph theory)
- Scores each edge's dependency strength with a weighted confidence model
- Estimates potential time savings using Monte Carlo simulation over historical task duration distributions
- Surfaces actionable, explained recommendations to users through the Airflow UI and a REST API
- Optionally auto-generates a corrected DAG with safer, parallelism-maximizing edge rewiring

The engine is designed to be non-destructive: it never modifies a live DAG without explicit user approval, and every recommendation comes with a confidence score and a plain-language explanation so the user can make an informed decision.

---

## 2. Problem Statement

### 2.1 How Users Write DAGs Today

When an engineer writes a new Airflow DAG, the natural instinct is to write tasks in the order they were designed — sequentially, top to bottom:

```python
fetch >> validate >> transform >> train >> report >> notify
```

This is correct about data flow but wrong about parallelism. Tasks like `train` and `report` may both only depend on `transform` output. The user has introduced a false dependency (`train >> report`) simply by placing them in sequence.

### 2.2 The Cost of Unnecessary Serialization

Given a pipeline where `train` takes 20 minutes and `report` takes 5 minutes:

- **User's DAG (sequential):** total time = 20 + 5 = 25 extra minutes after `transform`
- **Optimized DAG (parallel):** total time = max(20, 5) = 20 minutes after `transform`

That is a 20% end-to-end improvement on this segment alone. At scale, across hundreds of DAGs and thousands of daily runs, the aggregate compute waste and latency cost is significant.

### 2.3 Why This is Hard to Detect Reliably

The challenge is that Airflow itself cannot distinguish between:

- **Intentional serialization** — the user knows `report` should wait for `train` (e.g., for resource contention reasons, even if there is no data dependency)
- **Accidental serialization** — the user simply placed tasks in order without thinking about parallelism

Any engine that recommends removing an edge must therefore operate at a level of confidence high enough that false positives (wrongly suggesting to remove a real dependency) are extremely rare. This requires triangulating multiple independent signals.

### 2.4 The Uncertainty Dimension

Even when a recommendation is correct, the expected time savings is uncertain — because task durations are not fixed. A `train` task might take 18 minutes today and 35 minutes tomorrow depending on data volume. The engine must therefore express recommendations not as deterministic predictions but as probability distributions: "parallelizing these two tasks will save between 8 and 22 minutes with 85% confidence."

---

## 3. Goals and Non-Goals

### 3.1 Goals

- **G1:** Detect edges in user-defined DAGs that are likely not real data dependencies
- **G2:** Score each recommendation with a confidence value derived from multiple independent signals
- **G3:** Estimate expected time savings using uncertainty-aware simulation over historical duration data
- **G4:** Surface recommendations in the Airflow UI as non-blocking warnings the user can act on
- **G5:** Provide a REST API for programmatic access to recommendations
- **G6:** Generate corrected DAG code as a diff the user can review and apply
- **G7:** Work with any Airflow operator type — not just PythonOperator

### 3.2 Non-Goals

- **NG1:** Automatically modifying live DAGs without user approval
- **NG2:** Recommending task implementation changes (only DAG structure)
- **NG3:** Cross-DAG parallelization (single-DAG scope only in v1)
- **NG4:** Real-time analysis during DAG execution (offline analysis only)
- **NG5:** Supporting Airflow versions below 2.3

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Airflow Metadata DB                         │
│         (task_instance, xcom, dag_run, dag tables)                  │
└─────────────┬───────────────────────┬───────────────────────────────┘
              │ read                  │ read
              ▼                       ▼
┌─────────────────────┐   ┌───────────────────────┐
│  Dependency          │   │  Task Duration         │
│  Inference Layer     │   │  Profiler              │
│                      │   │                        │
│  - Dataset overlap   │   │  - Fits lognormal /    │
│  - Code analysis     │   │    gamma distributions │
│  - Timing corr.      │   │  - Per-task mean +     │
│  - Transitive check  │   │    variance + CI       │
└────────┬────────────┘   └──────────┬─────────────┘
         │                           │
         ▼                           ▼
┌─────────────────────────────────────────────────────┐
│               Confidence Scoring Engine              │
│                                                     │
│   score = Σ(signal_weight × signal_pass)            │
│   threshold_remove = 40                             │
│   threshold_uncertain = 65                          │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│               Monte Carlo Simulator                  │
│                                                     │
│   For each candidate parallelization:               │
│   - Sample 10,000 duration scenarios                │
│   - Compute makespan distribution                   │
│   - Output: P50 / P95 time savings estimate         │
└────────────────────────┬────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│              Recommendation Generator                │
│                                                     │
│   - Merges confidence score + time savings          │
│   - Builds plain-language explanation               │
│   - Generates DAG diff / rewrite suggestion         │
└──────────┬──────────────────────┬───────────────────┘
           │                      │
           ▼                      ▼
  ┌─────────────────┐    ┌────────────────────┐
  │  REST API        │    │  Airflow UI Plugin  │
  │  /v1/recommend   │    │  (DAG detail page   │
  │  /v1/simulate    │    │   warning banners)  │
  └─────────────────┘    └────────────────────┘
```

### 4.1 Data Flow Summary

1. A user triggers analysis (either on-demand via API or automatically after a DAG parse event)
2. The **Dependency Inference Layer** reads the DAG graph structure and historical run data and produces per-edge signal verdicts
3. The **Confidence Scoring Engine** combines signals into a single score per edge
4. The **Task Duration Profiler** fits probability distributions to each task's historical run times
5. The **Monte Carlo Simulator** uses those distributions to estimate time savings under proposed restructurings
6. The **Recommendation Generator** assembles the final output and generates the DAG diff
7. Results are stored in the engine's own database table and surfaced through the API and Airflow UI

### 4.2 Deployment Model

The engine runs as a separate Python service alongside the Airflow webserver. It shares read access to the Airflow metadata database but writes only to its own schema (`uapre_*` tables). It does not touch the Airflow scheduler or executor.

```
┌──────────────────────────────────────────────────────────┐
│  Host / Kubernetes Namespace                             │
│                                                          │
│  ┌─────────────────┐   ┌──────────────────────────────┐  │
│  │  Airflow Stack  │   │  Recommendation Engine       │  │
│  │  - Scheduler    │   │  - FastAPI service           │  │
│  │  - Webserver    │   │  - Analysis worker           │  │
│  │  - Workers      │   │  - Plugin (mounted into      │  │
│  │  - Metadata DB  │◄──│    Airflow webserver)        │  │
│  └─────────────────┘   └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## 5. Core Components

### 5.1 Dependency Inference Layer

This is the most critical component. It answers the question: **"Is the edge from task A to task B a real data dependency, or did the user just write them in sequence?"**

The layer runs four independent checks per edge and returns a signal verdict for each.

#### Signal 1: Dataset Overlap (weight 35%)

The strongest signal. If task A writes a dataset (file path, table name, S3 prefix) and task B reads a dataset, and those sets overlap, there is a real dependency.

**Source of truth — Airflow 2.4+ Dataset declarations:**

```python
from airflow.datasets import Dataset

# In the DAG definition
transform = PythonOperator(
    task_id="transform",
    outlets=[Dataset("s3://data/features/")]
)
report = PythonOperator(
    task_id="report",
    inlets=[Dataset("s3://data/features/")]
)
```

When Datasets are declared, overlap detection is exact:

```python
def check_dataset_overlap(dag, upstream_id: str, downstream_id: str) -> SignalResult:
    upstream_task   = dag.get_task(upstream_id)
    downstream_task = dag.get_task(downstream_id)

    up_outlets   = {d.uri for d in getattr(upstream_task, 'outlets', [])}
    down_inlets  = {d.uri for d in getattr(downstream_task, 'inlets', [])}

    overlap = up_outlets & down_inlets

    if overlap:
        return SignalResult(
            passed=True,
            explanation=f"Shared datasets: {overlap}"
        )

    # Fallback: scan XCom history for file path values
    xcom_paths = get_xcom_file_paths(upstream_id)
    code_reads  = extract_read_paths_from_code(downstream_task)
    inferred_overlap = match_paths(xcom_paths, code_reads)

    return SignalResult(
        passed=bool(inferred_overlap),
        explanation=f"Inferred overlap from XCom history: {inferred_overlap}" if inferred_overlap
                    else f"{downstream_id} reads {code_reads}; {upstream_id} writes {xcom_paths} — no overlap"
    )
```

**Fallback path matching** uses prefix normalization to handle cases like `s3://bucket/prefix/` matching `s3://bucket/prefix/file.parquet`:

```python
def match_paths(writes: set[str], reads: set[str]) -> set[str]:
    matched = set()
    for w in writes:
        for r in reads:
            # Normalize: strip trailing slash, compare prefixes
            w_norm = w.rstrip('/')
            r_norm = r.rstrip('/')
            if r_norm.startswith(w_norm) or w_norm.startswith(r_norm):
                matched.add(w)
    return matched
```

#### Signal 2: Static Code Analysis (weight 25%)

Parse the Python source of the downstream task and look for `xcom_pull` calls that reference the upstream `task_id`. This catches explicit XCom-based passing of values between tasks.

```python
import ast, inspect

def check_xcom_dependency(dag, upstream_id: str, downstream_id: str) -> SignalResult:
    task = dag.get_task(downstream_id)

    # Only applies to PythonOperator and its subclasses
    if not hasattr(task, 'python_callable'):
        return SignalResult(passed=False, explanation="Not a PythonOperator — skipping code analysis")

    try:
        source = inspect.getsource(task.python_callable)
        tree   = ast.parse(source)
    except (OSError, TypeError):
        return SignalResult(passed=False, explanation="Could not parse source code")

    class XComPullVisitor(ast.NodeVisitor):
        def __init__(self):
            self.found_upstream = False

        def visit_Call(self, node):
            # Match: ti.xcom_pull(task_ids='upstream_id') or context['ti'].xcom_pull(...)
            is_xcom_pull = (
                isinstance(node.func, ast.Attribute) and
                node.func.attr == 'xcom_pull'
            )
            if is_xcom_pull:
                for kw in node.keywords:
                    if kw.arg == 'task_ids':
                        val = kw.value
                        # Handle string literal, list of strings
                        if isinstance(val, ast.Constant) and val.value == upstream_id:
                            self.found_upstream = True
                        elif isinstance(val, ast.List):
                            for elt in val.elts:
                                if isinstance(elt, ast.Constant) and elt.value == upstream_id:
                                    self.found_upstream = True
            self.generic_visit(node)

    visitor = XComPullVisitor()
    visitor.visit(tree)

    return SignalResult(
        passed=visitor.found_upstream,
        explanation=(f"{downstream_id} calls xcom_pull(task_ids='{upstream_id}')"
                     if visitor.found_upstream
                     else f"No xcom_pull referencing '{upstream_id}' found in {downstream_id}")
    )
```

#### Signal 3: Timing Correlation (weight 20%)

If task B historically starts very shortly and consistently after task A ends, they are likely coupled. We use Pearson correlation between (task A end time) and (task B start time) across all historical runs.

```python
import numpy as np
from scipy.stats import pearsonr
from airflow.models import TaskInstance

def check_timing_correlation(dag_id: str, upstream_id: str,
                              downstream_id: str, session) -> SignalResult:
    runs = (
        session.query(TaskInstance)
        .filter(
            TaskInstance.dag_id == dag_id,
            TaskInstance.task_id.in_([upstream_id, downstream_id]),
            TaskInstance.state == 'success'
        )
        .order_by(TaskInstance.run_id)
        .all()
    )

    # Group by run_id and compute gap for each run
    from collections import defaultdict
    by_run = defaultdict(dict)
    for ti in runs:
        by_run[ti.run_id][ti.task_id] = ti

    gaps = []
    for run_id, tasks in by_run.items():
        if upstream_id in tasks and downstream_id in tasks:
            up_end   = tasks[upstream_id].end_date.timestamp()
            down_start = tasks[downstream_id].start_date.timestamp()
            gaps.append(down_start - up_end)

    if len(gaps) < 10:
        return SignalResult(
            passed=False,
            explanation=f"Only {len(gaps)} historical runs — not enough data for timing analysis"
        )

    gaps = np.array(gaps)
    mean_gap = gaps.mean()
    std_gap  = gaps.std()

    # Low mean gap (<10s) AND low variance indicates tight coupling
    # A high mean gap suggests they were queued independently
    tightly_coupled = mean_gap < 10.0 and std_gap < 5.0

    return SignalResult(
        passed=tightly_coupled,
        explanation=(
            f"Mean gap: {mean_gap:.1f}s, std: {std_gap:.1f}s — "
            f"{'tightly coupled (likely dependent)' if tightly_coupled else 'loose coupling (likely independent)'}"
        )
    )
```

#### Signal 4: Transitive Reduction Check (weight 20%)

If the edge A→B survives transitive reduction of the full graph, it is a minimal direct dependency. If it is removed by transitive reduction, then a longer path A→...→B already exists, meaning this edge is either redundant or artificially imposed.

```python
import networkx as nx

def check_transitive_reduction(G: nx.DiGraph, upstream: str, downstream: str) -> SignalResult:
    G_reduced = nx.transitive_reduction(G)
    survives  = G_reduced.has_edge(upstream, downstream)

    if survives:
        return SignalResult(
            passed=True,
            explanation="Edge survives transitive reduction — it is a minimal direct dependency"
        )

    # Find the actual path that makes this edge redundant
    all_paths = list(nx.all_simple_paths(G, upstream, downstream))
    bypass = min(all_paths, key=len) if all_paths else []

    return SignalResult(
        passed=False,
        explanation=(
            f"Edge is redundant — path {' → '.join(bypass)} already covers this dependency"
            if bypass else "Edge is redundant (a longer path exists)"
        )
    )
```

---

### 5.2 Topological Level Scheduler

After the Dependency Inference Layer has validated (or flagged) each edge, the Topological Level Scheduler computes the optimal parallel execution plan.

Every task is assigned a **level** equal to the length of the longest path from any root to that task. Tasks at the same level are provably independent and can be run concurrently.

```python
from collections import defaultdict
import networkx as nx

def compute_levels(G: nx.DiGraph) -> dict[str, int]:
    """
    Returns {task_id: level} where level 0 = roots (no upstream).
    Tasks at the same level can run in parallel.
    """
    levels = {}
    for task_id in nx.topological_sort(G):
        predecessors = list(G.predecessors(task_id))
        if not predecessors:
            levels[task_id] = 0
        else:
            levels[task_id] = max(levels[p] for p in predecessors) + 1
    return levels


def group_by_level(levels: dict[str, int]) -> dict[int, list[str]]:
    waves = defaultdict(list)
    for task_id, level in levels.items():
        waves[level].append(task_id)
    return dict(waves)


def compute_critical_path(G: nx.DiGraph, durations: dict[str, float]) -> list[str]:
    """
    Returns the sequence of tasks on the critical path — the longest
    chain by total expected duration. This is the sequence the engine
    should never try to parallelize further (it would have no effect).
    """
    # Add expected duration as edge weight (duration of the source node)
    for u, v in G.edges():
        G[u][v]['weight'] = durations.get(u, 0)

    # Longest path = critical path
    return nx.dag_longest_path(G, weight='weight')
```

**Example output for a 6-task pipeline:**

```
Wave 0: [fetch]               ← starts immediately
Wave 1: [validate]            ← waits for fetch
Wave 2: [transform]           ← waits for validate
Wave 3: [train, report]       ← BOTH start after transform finishes (parallel!)
Wave 4: [notify]              ← waits for train AND report
```

---

### 5.3 Transitive Reduction Module

Before computing levels or scoring edges, the engine runs transitive reduction to remove mathematically redundant edges. This is important because redundant edges can inflate a task's apparent level, making it look more dependent than it is.

```python
def clean_graph(G: nx.DiGraph) -> tuple[nx.DiGraph, list[tuple[str, str]]]:
    """
    Returns the minimally equivalent graph and the list of removed edges.
    """
    G_clean    = nx.transitive_reduction(G)
    removed    = list(set(G.edges()) - set(G_clean.edges()))

    # Copy node attributes from original graph
    for node in G_clean.nodes():
        G_clean.nodes[node].update(G.nodes[node])

    return G_clean, removed


def explain_removed_edges(G: nx.DiGraph, removed: list[tuple]) -> list[str]:
    explanations = []
    for u, v in removed:
        paths = list(nx.all_simple_paths(G, u, v))
        # The shortest path that isn't just [u, v] itself
        bypass_paths = [p for p in paths if len(p) > 2]
        if bypass_paths:
            shortest = min(bypass_paths, key=len)
            explanations.append(
                f"Edge {u} → {v} is redundant. "
                f"Path {' → '.join(shortest)} already covers this dependency. "
                f"Removing it may allow {v} to be scheduled earlier."
            )
    return explanations
```

---

### 5.4 Task Duration Profiler

To estimate time savings, the engine needs a probabilistic model of how long each task takes. It fits a statistical distribution to each task's historical durations.

```python
import numpy as np
from scipy import stats
from dataclasses import dataclass

@dataclass
class DurationProfile:
    task_id:     str
    dist_name:   str        # 'lognormal' | 'gamma' | 'normal'
    params:      tuple      # distribution parameters
    mean:        float      # seconds
    std:         float
    p5:          float      # 5th percentile
    p50:         float      # median
    p95:         float      # 95th percentile
    n_samples:   int        # number of historical runs used


CANDIDATE_DISTS = ['lognormal', 'gamma', 'norm', 'expon']

def fit_duration_profile(task_id: str, durations: list[float]) -> DurationProfile:
    """
    Fits multiple distributions and picks the best fit by AIC.
    Falls back to normal distribution if fewer than 20 samples.
    """
    durations = np.array([d for d in durations if d > 0])

    if len(durations) < 5:
        raise ValueError(f"Task {task_id} has fewer than 5 successful runs — cannot profile")

    if len(durations) < 20:
        # Use normal distribution for small samples
        mu, sigma = durations.mean(), durations.std()
        return DurationProfile(
            task_id=task_id, dist_name='norm', params=(mu, sigma),
            mean=mu, std=sigma,
            p5=np.percentile(durations, 5),
            p50=np.percentile(durations, 50),
            p95=np.percentile(durations, 95),
            n_samples=len(durations)
        )

    best_dist, best_params, best_aic = None, None, np.inf
    for dist_name in CANDIDATE_DISTS:
        dist = getattr(stats, dist_name)
        try:
            params = dist.fit(durations)
            log_likelihood = dist.logpdf(durations, *params).sum()
            k   = len(params)
            aic = 2 * k - 2 * log_likelihood
            if aic < best_aic:
                best_aic, best_dist, best_params = aic, dist_name, params
        except Exception:
            continue

    dist_obj = getattr(stats, best_dist)(*best_params)
    return DurationProfile(
        task_id=task_id, dist_name=best_dist, params=best_params,
        mean=float(dist_obj.mean()), std=float(dist_obj.std()),
        p5=float(dist_obj.ppf(0.05)),
        p50=float(dist_obj.ppf(0.50)),
        p95=float(dist_obj.ppf(0.95)),
        n_samples=len(durations)
    )


def load_historical_durations(dag_id: str, task_id: str, session,
                               last_n_runs: int = 200) -> list[float]:
    tis = (
        session.query(TaskInstance)
        .filter(
            TaskInstance.dag_id   == dag_id,
            TaskInstance.task_id  == task_id,
            TaskInstance.state    == 'success',
            TaskInstance.duration.isnot(None)
        )
        .order_by(TaskInstance.start_date.desc())
        .limit(last_n_runs)
        .all()
    )
    return [float(ti.duration) for ti in tis]
```

---

### 5.5 Monte Carlo Simulator

Given a proposed DAG restructuring (e.g., move `report` to run in parallel with `train`), the simulator estimates the expected time savings.

It does this by:
1. Sampling one duration for each task from its fitted distribution
2. Computing the makespan (total pipeline end time) under the current DAG structure
3. Computing the makespan under the proposed restructured DAG
4. Repeating 10,000 times and computing statistics over the savings distribution

```python
import numpy as np
from scipy import stats
import networkx as nx

N_SIMULATIONS = 10_000

def sample_duration(profile: DurationProfile) -> float:
    dist = getattr(stats, profile.dist_name)(*profile.params)
    return max(0.0, float(dist.rvs()))


def simulate_makespan(G: nx.DiGraph, profiles: dict[str, DurationProfile]) -> float:
    """
    Compute the total makespan of a DAG given one sampled duration per task.
    Uses a forward pass: each task starts when all its predecessors finish.
    """
    durations  = {tid: sample_duration(p) for tid, p in profiles.items()}
    finish_at  = {}

    for task_id in nx.topological_sort(G):
        preds = list(G.predecessors(task_id))
        start = max((finish_at[p] for p in preds), default=0.0)
        finish_at[task_id] = start + durations.get(task_id, 0.0)

    return max(finish_at.values())


def simulate_savings(G_current: nx.DiGraph,
                     G_proposed: nx.DiGraph,
                     profiles: dict[str, DurationProfile],
                     n: int = N_SIMULATIONS) -> dict:
    """
    Runs N_SIMULATIONS Monte Carlo iterations comparing current vs proposed DAG.
    Returns a summary of the savings distribution.
    """
    savings = np.zeros(n)
    for i in range(n):
        current_makespan  = simulate_makespan(G_current,  profiles)
        proposed_makespan = simulate_makespan(G_proposed, profiles)
        savings[i] = current_makespan - proposed_makespan

    positive_savings = savings[savings > 0]
    prob_improvement = len(positive_savings) / n

    return {
        'mean_savings_seconds':  float(savings.mean()),
        'p5_savings_seconds':    float(np.percentile(savings, 5)),
        'p50_savings_seconds':   float(np.percentile(savings, 50)),
        'p95_savings_seconds':   float(np.percentile(savings, 95)),
        'prob_improvement':      float(prob_improvement),
        'pct_improvement_mean':  None,   # filled by caller from current mean makespan
        'n_simulations':         n,
    }
```

**Interpreting the output:**

| Field | Meaning |
|-------|---------|
| `mean_savings_seconds` | Average time saved across all simulations |
| `p5_savings_seconds` | In 95% of scenarios, you save at least this much |
| `p95_savings_seconds` | In 5% of scenarios, you save this much (best case) |
| `prob_improvement` | Fraction of simulations where the proposed DAG was faster |

A recommendation is considered **high value** when `prob_improvement > 0.80` and `p5_savings_seconds > 60` (saves at least 1 minute even in pessimistic scenarios).

---

### 5.6 Confidence Scoring Engine

The Confidence Scoring Engine combines the four signal results into a single score per edge and assigns a verdict.

```python
from dataclasses import dataclass, field
from typing import Optional

SIGNAL_WEIGHTS = {
    'dataset_overlap':   35,
    'code_analysis':     25,
    'timing_correlation':20,
    'transitive_check':  20,
}

THRESHOLD_REMOVE    = 40   # score < 40  → recommend removing this edge
THRESHOLD_UNCERTAIN = 65   # 40 ≤ score < 65 → flag for manual review
                            # score ≥ 65 → keep the edge

@dataclass
class SignalResult:
    name:        str
    passed:      bool
    weight:      int
    explanation: str

    @property
    def score_contribution(self) -> int:
        return self.weight if self.passed else 0


@dataclass
class EdgeScore:
    from_task:      str
    to_task:        str
    signals:        list[SignalResult]
    total_score:    int
    verdict:        str           # 'keep' | 'uncertain' | 'remove'
    pass_count:     int
    suggested_fix:  Optional[str] = None
    time_savings:   Optional[dict] = None   # from Monte Carlo


def score_edge(from_task: str, to_task: str,
               signal_results: list[SignalResult],
               dag: 'nx.DiGraph',
               time_savings: Optional[dict] = None) -> EdgeScore:

    total = sum(s.score_contribution for s in signal_results)
    pass_count = sum(1 for s in signal_results if s.passed)

    if total < THRESHOLD_REMOVE:
        verdict = 'remove'
        fix = suggest_fix(from_task, to_task, dag)
    elif total < THRESHOLD_UNCERTAIN:
        verdict = 'uncertain'
        fix = f"Manually verify: does {to_task} require output produced by {from_task}?"
    else:
        verdict = 'keep'
        fix = None

    return EdgeScore(
        from_task=from_task, to_task=to_task,
        signals=signal_results, total_score=total, verdict=verdict,
        pass_count=pass_count, suggested_fix=fix, time_savings=time_savings
    )


def suggest_fix(from_task: str, to_task: str, dag: 'nx.DiGraph') -> str:
    """
    Find what to_task actually depends on, by checking inlets against
    all ancestors' outlets.
    """
    import networkx as nx
    ancestors = nx.ancestors(dag, to_task)
    real_parents = []
    for anc in ancestors:
        anc_task = dag.nodes[anc].get('task_obj')
        to_task_obj = dag.nodes[to_task].get('task_obj')
        if anc_task and to_task_obj:
            anc_outlets  = {d.uri for d in getattr(anc_task, 'outlets', [])}
            down_inlets  = {d.uri for d in getattr(to_task_obj, 'inlets', [])}
            if anc_outlets & down_inlets:
                real_parents.append(anc)

    if real_parents:
        return (
            f"Wire {to_task} to run after {real_parents} instead of {from_task}. "
            f"This allows {to_task} to start in parallel with any tasks that also "
            f"depend only on {real_parents}."
        )
    return (
        f"Remove the explicit {from_task} → {to_task} edge. "
        f"If {to_task} has no other declared dependencies on {from_task}'s output, "
        f"it can run as soon as its other real dependencies complete."
    )
```

---

### 5.7 Recommendation Generator

The Recommendation Generator assembles the final user-facing output by combining edge scores, time savings estimates, and plain-language explanations.

```python
from dataclasses import dataclass
from typing import Optional
import json

@dataclass
class Recommendation:
    dag_id:             str
    edge:               tuple[str, str]
    confidence_score:   int
    verdict:            str
    pass_count:         int
    total_signals:      int
    signal_details:     list[dict]
    suggested_fix:      Optional[str]
    time_savings:       Optional[dict]
    plain_explanation:  str
    dag_diff:           Optional[str]    # Python code diff to fix the DAG


def generate_recommendation(edge_score: EdgeScore,
                             dag_id: str,
                             dag_source: str) -> Recommendation:
    signal_details = [
        {
            'name':          s.name,
            'passed':        s.passed,
            'weight':        s.weight,
            'contribution':  s.score_contribution,
            'explanation':   s.explanation,
        }
        for s in edge_score.signals
    ]

    explanation = build_explanation(edge_score)
    diff        = build_dag_diff(edge_score, dag_source) if edge_score.verdict == 'remove' else None

    return Recommendation(
        dag_id=dag_id,
        edge=(edge_score.from_task, edge_score.to_task),
        confidence_score=edge_score.total_score,
        verdict=edge_score.verdict,
        pass_count=edge_score.pass_count,
        total_signals=len(edge_score.signals),
        signal_details=signal_details,
        suggested_fix=edge_score.suggested_fix,
        time_savings=edge_score.time_savings,
        plain_explanation=explanation,
        dag_diff=diff
    )


def build_explanation(edge_score: EdgeScore) -> str:
    from_t, to_t = edge_score.from_task, edge_score.to_task
    lines = []

    if edge_score.verdict == 'remove':
        lines.append(
            f"The engine recommends removing the {from_t} → {to_t} dependency "
            f"(confidence score: {edge_score.total_score}/100)."
        )
        failed = [s for s in edge_score.signals if not s.passed]
        lines.append(f"Reason: {len(failed)} of {len(edge_score.signals)} signals found no evidence of a real dependency:")
        for s in failed:
            lines.append(f"  - {s.name}: {s.explanation}")
        if edge_score.suggested_fix:
            lines.append(f"Suggestion: {edge_score.suggested_fix}")
        if edge_score.time_savings:
            ts = edge_score.time_savings
            lines.append(
                f"Expected time saving: {ts['p50_savings_seconds']/60:.1f} min (median), "
                f"{ts['p5_savings_seconds']/60:.1f}–{ts['p95_savings_seconds']/60:.1f} min range, "
                f"with {ts['prob_improvement']*100:.0f}% probability of improvement."
            )
    elif edge_score.verdict == 'uncertain':
        lines.append(
            f"The engine is uncertain about the {from_t} → {to_t} edge "
            f"(score: {edge_score.total_score}/100). "
            f"Manual review is recommended."
        )
    else:
        lines.append(
            f"The {from_t} → {to_t} edge appears to be a real dependency "
            f"(score: {edge_score.total_score}/100). Keep it."
        )

    return ' '.join(lines)


def build_dag_diff(edge_score: EdgeScore, dag_source: str) -> str:
    """
    Generates a unified diff of the DAG Python file showing the edge change.
    """
    from_t, to_t = edge_score.from_task, edge_score.to_task
    old_line = f"{from_t} >> {to_t}"
    # Find the real parent from the suggested fix, if available
    # This is a simplified diff — real implementation uses ast rewriting
    diff = (
        f"# Suggested change to your DAG:\n"
        f"# Remove: {old_line}\n"
        f"# Apply:  {edge_score.suggested_fix or 'Remove this explicit dependency'}\n\n"
        f"# Before:\n"
        f"# {from_t} >> {to_t}\n\n"
        f"# After (example — verify real parent):\n"
        f"# transform >> {to_t}  # {to_t} only needs transform output\n"
        f"# transform >> {from_t}  # {from_t} continues unchanged\n"
    )
    return diff
```

---

### 5.8 DAG Rewriter

For users who accept a recommendation, the DAG Rewriter produces a corrected Python file using AST manipulation — not string replacement, which is fragile.

```python
import ast
import astor   # pip install astor

class DAGRewriter(ast.NodeTransformer):
    """
    Rewrites the DAG source file to remove a specific edge
    and optionally add a replacement edge.
    """

    def __init__(self, remove_edge: tuple[str, str],
                 add_edge: Optional[tuple[str, str]] = None):
        self.remove_from, self.remove_to = remove_edge
        self.add_edge = add_edge

    def visit_BinOp(self, node):
        """
        Airflow edges are written as:  task_a >> task_b
        Which is a BinOp with op=RShift.
        """
        self.generic_visit(node)

        if not isinstance(node.op, ast.RShift):
            return node

        left_name  = self._get_name(node.left)
        right_name = self._get_name(node.right)

        if left_name == self.remove_from and right_name == self.remove_to:
            # Remove this edge by returning None (ast.NodeTransformer removes None nodes)
            return None

        return node

    def _get_name(self, node) -> Optional[str]:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None


def rewrite_dag(source: str,
                remove_edge: tuple[str, str],
                add_edge: Optional[tuple[str, str]] = None) -> str:
    tree = ast.parse(source)
    rewriter = DAGRewriter(remove_edge, add_edge)
    new_tree = rewriter.visit(tree)
    ast.fix_missing_locations(new_tree)

    new_source = astor.to_source(new_tree)

    if add_edge:
        # Append the new edge definition at the end of the file
        new_source += f"\n{add_edge[0]} >> {add_edge[1]}  # Added by recommendation engine\n"

    return new_source
```

---

### 5.9 Airflow Plugin and UI Integration

The engine ships as an Airflow plugin that adds a new tab to the DAG detail page showing recommendations inline.

```python
from airflow.plugins_manager import AirflowPlugin
from flask import Blueprint
from flask_appbuilder import BaseView, expose
import requests

rec_blueprint = Blueprint(
    'recommendation_engine',
    __name__,
    template_folder='templates',
    static_folder='static',
    static_url_path='/static/recommendation_engine'
)


class RecommendationView(BaseView):
    default_view = 'recommendations'

    @expose('/recommendations/<dag_id>')
    def recommendations(self, dag_id):
        # Fetch from the engine's REST API
        resp = requests.get(
            f"http://localhost:8090/v1/recommend/{dag_id}",
            timeout=10
        )
        recs = resp.json() if resp.ok else []

        # Filter to only actionable ones
        actionable = [r for r in recs if r['verdict'] in ('remove', 'uncertain')]

        return self.render_template(
            'recommendation_engine/recommendations.html',
            dag_id=dag_id,
            recommendations=actionable,
            total_edges=len(recs),
        )


class RecommendationPlugin(AirflowPlugin):
    name = 'recommendation_engine'
    flask_blueprints = [rec_blueprint]
    appbuilder_views = [{
        'name': 'Parallelization Tips',
        'category': 'DAG Tools',
        'view': RecommendationView(),
    }]
```

The UI template surfaces recommendations as color-coded banners on the DAG detail page:

```html
<!-- templates/recommendation_engine/recommendations.html -->
{% for rec in recommendations %}
<div class="alert {% if rec.verdict == 'remove' %}alert-warning{% else %}alert-info{% endif %}">
  <strong>{{ rec.edge[0] }} → {{ rec.edge[1] }}</strong>
  — Confidence score: {{ rec.confidence_score }}/100
  <p>{{ rec.plain_explanation }}</p>
  {% if rec.time_savings %}
  <p>Estimated saving: {{ (rec.time_savings.p50_savings_seconds / 60) | round(1) }} min
     ({{ (rec.time_savings.prob_improvement * 100) | round }}% probability)</p>
  {% endif %}
  <a href="/recommendation_engine/diff/{{ dag_id }}/{{ rec.edge[0] }}/{{ rec.edge[1] }}">
    View suggested DAG change
  </a>
</div>
{% endfor %}
```

---

## 6. Data Models

### 6.1 Engine Database Schema

The engine maintains its own tables in the Airflow metadata database under the `uapre_` prefix. It never writes to Airflow's own tables.

```sql
-- Stores the result of analyzing each edge in a DAG
CREATE TABLE uapre_edge_analysis (
    id              SERIAL PRIMARY KEY,
    dag_id          VARCHAR(250) NOT NULL,
    from_task       VARCHAR(250) NOT NULL,
    to_task         VARCHAR(250) NOT NULL,
    analyzed_at     TIMESTAMP   NOT NULL DEFAULT NOW(),
    confidence_score INTEGER     NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
    verdict         VARCHAR(20)  NOT NULL CHECK (verdict IN ('keep', 'uncertain', 'remove')),
    pass_count      INTEGER      NOT NULL,
    signal_results  JSONB        NOT NULL,   -- array of SignalResult objects
    suggested_fix   TEXT,
    plain_explanation TEXT        NOT NULL,
    dag_diff        TEXT,
    UNIQUE (dag_id, from_task, to_task, analyzed_at)
);

-- Stores fitted duration profiles per task
CREATE TABLE uapre_duration_profile (
    id              SERIAL PRIMARY KEY,
    dag_id          VARCHAR(250) NOT NULL,
    task_id         VARCHAR(250) NOT NULL,
    profiled_at     TIMESTAMP   NOT NULL DEFAULT NOW(),
    dist_name       VARCHAR(50)  NOT NULL,
    dist_params     JSONB        NOT NULL,
    mean_seconds    FLOAT        NOT NULL,
    std_seconds     FLOAT        NOT NULL,
    p5_seconds      FLOAT        NOT NULL,
    p50_seconds     FLOAT        NOT NULL,
    p95_seconds     FLOAT        NOT NULL,
    n_samples       INTEGER      NOT NULL,
    UNIQUE (dag_id, task_id)
);

-- Stores Monte Carlo simulation results per proposed restructuring
CREATE TABLE uapre_simulation_result (
    id                      SERIAL PRIMARY KEY,
    dag_id                  VARCHAR(250) NOT NULL,
    edge_analysis_id        INTEGER REFERENCES uapre_edge_analysis(id),
    simulated_at            TIMESTAMP   NOT NULL DEFAULT NOW(),
    n_simulations           INTEGER     NOT NULL,
    mean_savings_seconds    FLOAT       NOT NULL,
    p5_savings_seconds      FLOAT       NOT NULL,
    p50_savings_seconds     FLOAT       NOT NULL,
    p95_savings_seconds     FLOAT       NOT NULL,
    prob_improvement        FLOAT       NOT NULL CHECK (prob_improvement BETWEEN 0 AND 1)
);

-- Tracks which recommendations users acted on
CREATE TABLE uapre_recommendation_feedback (
    id                  SERIAL PRIMARY KEY,
    edge_analysis_id    INTEGER REFERENCES uapre_edge_analysis(id),
    feedback_at         TIMESTAMP   NOT NULL DEFAULT NOW(),
    action              VARCHAR(20)  NOT NULL CHECK (action IN ('applied', 'dismissed', 'snoozed')),
    applied_by          VARCHAR(250),
    notes               TEXT
);
```

### 6.2 API Response Schema

```json
{
  "dag_id": "my_ml_pipeline",
  "analyzed_at": "2025-04-18T10:23:00Z",
  "total_edges": 5,
  "recommendations": [
    {
      "edge": ["train", "report"],
      "confidence_score": 20,
      "verdict": "remove",
      "pass_count": 1,
      "total_signals": 4,
      "signal_details": [
        {
          "name": "dataset_overlap",
          "passed": false,
          "weight": 35,
          "contribution": 0,
          "explanation": "report reads s3://features/, train writes s3://models/ — no overlap"
        },
        {
          "name": "code_analysis",
          "passed": false,
          "weight": 25,
          "contribution": 0,
          "explanation": "No xcom_pull referencing 'train' found in report task"
        },
        {
          "name": "timing_correlation",
          "passed": false,
          "weight": 20,
          "contribution": 0,
          "explanation": "Mean gap: 18.3s, std: 12.1s — loose coupling (likely independent)"
        },
        {
          "name": "transitive_check",
          "passed": true,
          "weight": 20,
          "contribution": 20,
          "explanation": "Edge survives transitive reduction"
        }
      ],
      "suggested_fix": "Wire report after transform instead of train. This allows report to run in parallel with train.",
      "time_savings": {
        "mean_savings_seconds": 742,
        "p5_savings_seconds": 310,
        "p50_savings_seconds": 680,
        "p95_savings_seconds": 1420,
        "prob_improvement": 0.92,
        "n_simulations": 10000
      },
      "plain_explanation": "The engine recommends removing the train → report dependency (confidence: 20/100). Three of four signals found no evidence of a real dependency. report only needs transform output. Expected time saving: 11.3 min median, 5.2–23.7 min range, with 92% probability of improvement.",
      "dag_diff": "# Remove: train >> report\n# Add:    transform >> report"
    }
  ]
}
```

---

## 7. Algorithm Details

### 7.1 Full Analysis Pipeline (Pseudocode)

```
function analyze_dag(dag_id):
    dag       = load_dag(dag_id)
    G         = build_networkx_graph(dag)
    G_clean   = transitive_reduction(G)
    redundant = edges(G) - edges(G_clean)

    profiles  = {}
    for task in dag.tasks:
        durations = load_historical_durations(dag_id, task.task_id)
        if len(durations) >= 5:
            profiles[task.task_id] = fit_duration_profile(task.task_id, durations)

    recommendations = []
    for edge (u, v) in G_clean.edges():
        signals = [
            check_dataset_overlap(dag, u, v),
            check_xcom_dependency(dag, u, v),
            check_timing_correlation(dag_id, u, v),
            check_transitive_reduction(G_clean, u, v)
        ]
        edge_score = score_edge(u, v, signals, G_clean)

        if edge_score.verdict == 'remove':
            G_proposed     = G_clean.copy()
            real_parent    = find_real_parent(dag, u, v)
            remove_edge(G_proposed, u, v)
            if real_parent and real_parent != u:
                add_edge(G_proposed, real_parent, v)
            savings = simulate_savings(G_clean, G_proposed, profiles)
            edge_score.time_savings = savings

        rec = generate_recommendation(edge_score, dag_id, dag.fileloc)
        recommendations.append(rec)
        store(rec)

    # Also report redundant edges found before reduction
    for (u, v) in redundant:
        emit_redundancy_warning(u, v, G)

    return recommendations
```

### 7.2 Confidence Score Thresholds — Rationale

| Score range | Verdict | Reasoning |
|-------------|---------|-----------|
| 0–39 | Remove | At least 3 of 4 signals show no dependency. Dataset overlap (35%) alone failing is very strong evidence. False positive rate estimated at <2%. |
| 40–64 | Uncertain | Mixed signals — likely a dependency that is hard to detect automatically (e.g., side-effects, shared state). Human review is cheaper than a wrong recommendation. |
| 65–100 | Keep | At least 2 major signals confirm a real dependency. False negative rate (missing a real dependency) estimated at <1%. |

---

## 8. Uncertainty Modeling

### 8.1 Why Point Estimates Are Insufficient

A naive recommendation engine might say: "Parallelizing `train` and `report` will save 12 minutes." But this ignores that `train` sometimes takes 8 minutes (light data day) and sometimes 40 minutes (heavy data day). The expected saving is therefore a distribution, not a point estimate.

### 8.2 Lognormal Distribution Rationale

Task durations are almost always right-skewed: they have a minimum (startup time), a typical value (normal operation), and a long tail (occasional slow runs due to data volume spikes, retries, or infrastructure issues). The lognormal distribution captures this shape naturally and never produces negative values.

For tasks with short histories (<20 runs), the engine falls back to a normal distribution with a warning that estimates are less reliable.

### 8.3 Uncertainty in the Recommendation Itself

The confidence score is itself uncertain because each signal has its own error rate:

- **Dataset overlap** can be wrong if a task writes to a path not captured in inlets/outlets declarations or XCom
- **Code analysis** can be wrong for dynamically constructed XCom pull calls
- **Timing correlation** can be misleading if two independent tasks happen to always start together due to scheduler behavior

The engine addresses this by being conservative: it only recommends removing edges when the score is clearly below the threshold (< 40), and flags anything in the 40–65 range for human review rather than making a potentially wrong automatic recommendation.

### 8.4 Monte Carlo Simulation Details

The simulator samples independently from each task's distribution in each simulation run. This treats task durations as independent random variables, which is a simplification — in reality, heavy data days affect all tasks simultaneously. A future improvement would model the correlation structure between task durations (e.g., using a multivariate lognormal).

```python
# Correlated sampling (future improvement using Cholesky decomposition)
def sample_correlated_durations(profiles, correlation_matrix):
    import numpy as np
    means  = np.array([p.mean for p in profiles.values()])
    stds   = np.array([p.std  for p in profiles.values()])
    L      = np.linalg.cholesky(correlation_matrix)
    z      = np.random.standard_normal(len(profiles))
    corr_z = L @ z
    return {tid: max(0, m + s * c)
            for (tid, p), m, s, c in zip(profiles.items(), means, stds, corr_z)}
```

---

## 9. API Design

The engine exposes a REST API built with FastAPI.

### Endpoints

```
GET  /v1/recommend/{dag_id}
     Returns all recommendations for a DAG.
     Query params:
       verdict=remove|uncertain|keep   (filter by verdict)
       min_confidence=0..100           (filter by score floor)
       min_savings_seconds=N           (filter by p50 savings)

GET  /v1/recommend/{dag_id}/{from_task}/{to_task}
     Returns the recommendation for a specific edge.

POST /v1/analyze/{dag_id}
     Triggers a fresh analysis (async). Returns a job_id.
     Body: { "force_refresh": true }

GET  /v1/analyze/status/{job_id}
     Returns the status of an async analysis job.

GET  /v1/simulate/{dag_id}
     Returns the full makespan simulation for the current DAG
     and the optimized DAG (all recommendations applied).

GET  /v1/diff/{dag_id}
     Returns the suggested Python code diff for all 'remove' recommendations.

POST /v1/feedback/{dag_id}/{from_task}/{to_task}
     Records user feedback on a recommendation.
     Body: { "action": "applied|dismissed|snoozed", "notes": "..." }

GET  /v1/profiles/{dag_id}
     Returns fitted duration profiles for all tasks in a DAG.
```

### Authentication

The API uses the same authentication as the Airflow webserver. Requests from the Airflow UI plugin pass through the Airflow session cookie. External callers use API key authentication via the `X-API-Key` header.

---

## 10. Testing Strategy

### 10.1 Unit Tests

Each signal checker has its own unit test suite with mocked DAGs and TaskInstance data.

```python
# tests/test_dataset_overlap.py
def test_dataset_overlap_exact_match():
    dag = build_mock_dag([
        ('transform', outlets=['s3://features/']),
        ('report',    inlets=['s3://features/']),
    ])
    result = check_dataset_overlap(dag, 'transform', 'report')
    assert result.passed is True

def test_dataset_overlap_no_match():
    dag = build_mock_dag([
        ('train',  outlets=['s3://models/']),
        ('report', inlets=['s3://features/']),
    ])
    result = check_dataset_overlap(dag, 'train', 'report')
    assert result.passed is False

def test_confidence_score_all_fail():
    signals = [
        SignalResult('dataset_overlap',    False, 35, ''),
        SignalResult('code_analysis',      False, 25, ''),
        SignalResult('timing_correlation', False, 20, ''),
        SignalResult('transitive_check',   False, 20, ''),
    ]
    score = score_edge('train', 'report', signals, G)
    assert score.total_score == 0
    assert score.verdict == 'remove'
```

### 10.2 Integration Tests

Integration tests use a real SQLite Airflow metadata DB with synthetic TaskInstance records representing 100 historical runs per task.

### 10.3 Simulation Accuracy Tests

The Monte Carlo simulator is tested against analytically-computable ground truth cases. For a two-task pipeline where both tasks are lognormal with known parameters, the expected makespan can be computed exactly using convolution — the simulator's output should match to within 2%.

### 10.4 False Positive Rate Validation

We maintain a curated set of 50 DAGs where we know ground truth (all edges are either confirmed real or confirmed accidental, from code review). We run the engine against these and verify:

- **False positive rate** (wrongly recommends removing a real edge): target < 2%
- **False negative rate** (misses a removable edge): target < 15% (acceptable — conservative is better than wrong)
- **Uncertain rate**: target < 20% of all edges

---

## 11. Deployment and Operations

### 11.1 Infrastructure Requirements

| Component | Spec |
|-----------|------|
| Analysis worker | 2 vCPU, 4 GB RAM |
| FastAPI service | 1 vCPU, 1 GB RAM |
| DB storage | ~100 MB per 1,000 DAG analyses |
| Python version | 3.10+ |
| Airflow version | 2.3+ (2.4+ for Dataset scheduling) |

### 11.2 Python Dependencies

```
apache-airflow>=2.3.0
networkx>=3.0
scipy>=1.10
numpy>=1.24
fastapi>=0.100
sqlalchemy>=1.4
astor>=0.8           # for DAG rewriting
```

### 11.3 Configuration

```yaml
# recommendation_engine.yaml
engine:
  n_simulations: 10000
  min_history_runs: 10         # minimum runs before profiling a task
  threshold_remove: 40
  threshold_uncertain: 65
  signal_weights:
    dataset_overlap:   35
    code_analysis:     25
    timing_correlation: 20
    transitive_check:  20

scheduler:
  auto_analyze_on_dag_update: true
  reanalysis_interval_hours: 24

api:
  host: 0.0.0.0
  port: 8090
  auth_mode: airflow_session    # or 'api_key'
```

### 11.4 Observability

The engine emits structured logs and Prometheus metrics:

```
uapre_analyses_total{dag_id, verdict}       Counter
uapre_analysis_duration_seconds{dag_id}     Histogram
uapre_simulation_duration_seconds           Histogram
uapre_recommendations_applied_total{dag_id} Counter
uapre_false_positive_reports_total          Counter  # from user feedback
```

---

## 12. Limitations and Future Work

### 12.1 Known Limitations

**L1 — Dynamic task generation.** Tasks created with `@task` groups or `TaskGroup` with dynamic expansion may not have their code introspectable at analysis time. Signal 2 (code analysis) falls back to a neutral result in these cases.

**L2 — Operator opacity.** For non-PythonOperator tasks (e.g., `BashOperator`, `SparkSubmitOperator`), code analysis is not possible. The engine relies on dataset declarations and timing signals only, reducing confidence ceiling to 75/100 for such edges.

**L3 — Correlated durations.** The Monte Carlo simulator treats task durations as independent. On heavy data days, all tasks are slower simultaneously, meaning the simulator may overestimate savings in high-variance scenarios.

**L4 — Single-DAG scope.** Cross-DAG dependencies (e.g., a `TriggerDagRunOperator`) are not analyzed in v1.

**L5 — Resource contention edges.** Some users intentionally serialize tasks not for data reasons but to prevent resource contention (e.g., both tasks hit the same database). The engine cannot detect this intent and may incorrectly flag such edges.

### 12.2 Future Work

**F1 — LLM-assisted explanation.** Use a language model to generate richer, context-aware explanations of why an edge is suspicious, referencing the actual code and data path names.

**F2 — Cross-DAG analysis.** Extend scope to analyze dependencies across DAGs linked by `TriggerDagRunOperator` or `ExternalTaskSensor`.

**F3 — Correlated duration modeling.** Introduce a Gaussian copula to model the correlation structure of task durations within a run, producing more accurate savings estimates on volatile pipelines.

**F4 — Proactive suggestions.** Rather than just flagging existing edges, suggest new edges the user may have forgotten to declare (cases where task B reads data that task A writes, but no explicit edge exists).

**F5 — Feedback loop retraining.** Use the `uapre_recommendation_feedback` table to periodically recalibrate signal weights based on which recommendations users accepted vs. dismissed.

**F6 — Resource-aware parallelism.** Integrate with Airflow's pool and slot system to recommend not just structural parallelism but also optimal pool sizing for the recommended wave structure.

---

*End of design document. Version 1.0.*
