.. Licensed to the Apache Software Foundation (ASF) under one
   or more contributor license agreements.  See the NOTICE file
   distributed with this work for additional information
   regarding copyright ownership.  The ASF licenses this file
   to you under the Apache License, Version 2.0 (the
   "License"); you may not use this file except in compliance
   with the License.  You may obtain a copy of the License at

..    http://www.apache.org/licenses/LICENSE-2.0

.. Unless required by applicable law or agreed to in writing,
   software distributed under the License is distributed on an
   "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
   KIND, either express or implied.  See the License for the
   specific language governing permissions and limitations
   under the License.

# UAPE v2 — Implementation Notes

**Status:** Implemented (dev provider)  
**Provider:** `apache-airflow-providers-uape` v0.2.0  
**Code location:** `dev/uape-provider/`  
**Design reference:** `AIP-08-dag-optimizer-uncertainty-aware-parallelization-v2.md`  
**Supersedes:** `AIP-08-dag-optimizer-uncertainty-aware-parallelization-v1.md`

### Revision history

| Date | Change |
|------|--------|
| Initial | v2 engine: 4 signals, scoring, Monte Carlo, FastAPI UI |
| Follow-up | Fix UI list blank on first render (DOM ordering + captured element refs) |
| Follow-up | Replace in-process dict cache with DB-backed `uape_report_cache` table |

---

## 1. What Was Built

The UAPE v2 provider implements the core pipeline from the design doc as a self-contained
Airflow development provider. It ships three surfaces:

| Surface | Entry point |
|---------|-------------|
| CLI | `airflow uape analyze <dag_id>` / `airflow uape export <dag_id>` |
| REST API | `GET /uape/dags/{dag_id}/recommendations.json` |
| Airflow UI tab | Recommendations panel → iframe at `/uape/dags/{dag_id}/recommendations-ui` |

All three surfaces are powered by the same analysis engine in
`airflow/providers/uape/parallelization.py`. Analysis results are cached in the metadata DB
(see §2.8) so expensive work is not repeated between page loads. The provider is read-only with
respect to DAG content: it never modifies any DAG or task-instance record.

---

## 2. Design → Implementation Mapping

### 2.1 Dependency Inference Layer (§5.1)

All four signals from the design doc are implemented.

#### Signal 1 — Asset / Dataset overlap (weight 35 %)

```python
def signal_asset_overlap(task_dict, upstream_id, downstream_id) -> SignalResult
```

Reads `task.outlets` and `task.inlets` from the **serialized DAG** (no DB query needed).
Works with both `Asset` (Airflow 3) and `Dataset` (Airflow 2.4+) objects via `getattr(item, "uri", None)`.

Matching is **prefix-based**: `s3://bucket/prefix/` matches `s3://bucket/prefix/2024/data.parquet`.
This covers the common pattern where a producer writes to a prefix and a consumer reads any file
under it.

**Skipped** when either task is absent from `task_dict`. **Not skipped** (but `passed=False`) when
both tasks exist but declare no assets — absence of asset declarations is evidence of no data
dependency.

#### Signal 2 — XCom code analysis (weight 25 %)

```python
def signal_xcom_analysis(task_dict, upstream_id, downstream_id) -> SignalResult
```

Calls `inspect.getsource(task.python_callable)` and walks the AST for:

```python
ti.xcom_pull(task_ids="<upstream_id>")
ti.xcom_pull(task_ids=["<upstream_id>", ...])
```

**Skipped** when the downstream task has no `python_callable` (e.g. BashOperator, sensors) or
when `inspect.getsource()` raises `OSError` / `TypeError` (lambdas, built-ins, callables whose
source file is not accessible at analysis time).

Skipping is intentional: a missing source does not mean the dependency is real. The signal is
excluded from the score denominator so it does not penalise real dependencies.

**Limitation vs design doc:** Dynamic XCom pulls (`xcom_pull(task_ids=some_variable)`) are not
detected. The signal only matches string and list literals in the AST.

#### Signal 3 — Timing correlation (weight 20 %)

```python
def signal_timing_correlation(dag_id, upstream_id, downstream_id, session) -> SignalResult
```

Queries `TaskInstance` (success state only) and computes the distribution of the gap between
`upstream.end_date` and `downstream.start_date` across historical runs.

**Threshold:** mean gap < 10 s **and** std < 5 s → `passed=True` (tight coupling, likely dependent).

Requires `session` (SQLAlchemy session passed from CLI/web endpoint). **Skipped** when `session=None`
or when fewer than `MIN_TIMING_RUNS = 10` successful paired runs exist.

**Deviation from design doc:** The design describes a Pearson correlation between end-time and
start-time vectors. The implementation uses the gap distribution (mean + std) instead, which is
more directly interpretable and avoids false positives from correlated external schedules (e.g.
both tasks always run at 02:00 UTC regardless of dependency).

#### Signal 4 — Transitive reduction (weight 20 %)

```python
def signal_transitive_reduction(adj, upstream_id, downstream_id) -> SignalResult
```

Builds a `networkx.DiGraph` from the full task adjacency list and calls
`networkx.transitive_reduction()`. An edge that does **not** survive reduction has a longer path
covering it — it is structurally redundant.

`passed=True` when the edge survives (minimal direct dependency). `passed=False` when removed by
reduction, with the bypass path shown in the explanation (e.g. `A → B → C` makes `A → C`
redundant).

**Skipped** only when `networkx` is not installed (it is a required dependency in v0.2.0, so this
path should not be hit in normal use).

### 2.2 Confidence Scoring Engine (§5.6)

```python
def score_edge(from_task, to_task, signals) -> EdgeScore
```

Implements the weighted-sum model from the design doc with one extension: **skipped signals are
excluded from the denominator**.

```
score = Σ(weight_i × passed_i) / Σ(weight_i for non-skipped signals) × 100
```

This prevents a missing timing history or non-PythonOperator callable from artificially
depressing the score of a real dependency.

Thresholds (unchanged from design):

| Score | Verdict | Meaning |
|-------|---------|---------|
| < 40 | `remove` | Likely false dependency — recommend removal |
| 40 – 64 | `uncertain` | Mixed signals — flag for manual review |
| ≥ 65 | `keep` | Real dependency detected |

When all signals are skipped (no data available at all), the score defaults to 50 → `uncertain`.

### 2.3 Task Duration Profiler (§5.4)

```python
def fit_duration_profile(task_id, durations) -> DurationProfile | None
```

Fits three candidate distributions — `lognorm`, `gamma`, `norm` — using `scipy.stats.*.fit()` and
selects the best by AIC (Akaike Information Criterion). Falls back to empirical percentiles when
`scipy` is not installed.

Requires `MIN_PROFILE_RUNS = 5` successful historical durations. Returns `None` otherwise (the
Monte Carlo step is skipped gracefully).

### 2.4 Monte Carlo Simulator (§5.5)

```python
def simulate_savings(task_ids, adj_current, adj_proposed, profiles, n) -> SimulationResult | None
```

Runs `N_SIMULATIONS = 10 000` iterations by default.

**Vectorised implementation:** all durations for all tasks are sampled simultaneously using
`numpy.random.default_rng().normal()` (or `scipy.stats.*.rvs()` when a fitted distribution is
available), producing arrays of shape `(n_tasks, n_simulations)`. The makespan forward-pass is
fully vectorised across simulations:

```python
finish[tid] = np.maximum.reduce([finish[p] for p in predecessors]) + samples[tid]
```

This is orders of magnitude faster than a Python loop over 10 000 runs.

**Output:** `p5`, `p50`, `p95` savings (seconds) and `prob_improvement` (fraction of simulations
where the proposed DAG finishes sooner).

Simulation is **only run for `remove` verdicts** and only when at least one task in the subgraph
has a fitted duration profile. Edges with `uncertain` or `keep` verdicts skip simulation.

### 2.5 Recommendation Generator (§5.7)

For each `remove` edge, `_find_suggested_fix()` looks for the actual upstream parent of the
downstream task by checking asset URI overlap against all other tasks in the DAG. If a real
parent is found, the suggestion names it explicitly:

> "Wire `report` after `transform` instead of `train`. This allows `report` to start in parallel
> with tasks that also depend only on `transform`."

A `dag_diff` field is also included — a commented pseudo-diff showing the `Before / After` change
in Python DAG code.

### 2.6 Result Cache (`uape_report_cache` table)

Analysis is expensive — 10 000 Monte Carlo iterations, AST parsing, and DB queries against
`TaskInstance`. Repeating it on every page load would be wasteful, especially because results
only change when either the DAG definition or the run history changes.

**Mechanism:**

```
On each request to /recommendations.json:
  1. Fetch SerializedDagModel.last_updated   (1 indexed column read)
  2. Fetch MAX(DagRun.start_date) for dag_id (1 aggregate query)
  3. Compute SHA-256("dag_last_updated|latest_run_start")
  4. Compare against content_hash in uape_report_cache
     HIT  → return json.loads(report_json)      # < 1 ms
     MISS → run full analysis, upsert row, return result
```

**Table schema (`UapeReportCache` SQLAlchemy model in `web/app.py`):**

| Column | Type | Purpose |
|--------|------|---------|
| `dag_id` | `String(250)` PK | One row per DAG |
| `content_hash` | `String(64)` | SHA-256 of the two source timestamps |
| `report_json` | `Text` | Full serialised JSON report |
| `cached_at` | `UtcDateTime` | `dag.last_updated` at cache-write time (human-readable marker) |

**Table creation:** `UapeReportCache.__table__.create(engine, checkfirst=True)` runs once
in the FastAPI `startup` event — no Alembic migration needed.

**Invalidation triggers:**

| Event | `last_updated` | `MAX(start_date)` | Action |
|-------|---------------|--------------------|--------|
| Page refresh, nothing changed | same | same | Return cached result |
| DAG file edited and re-parsed | changes | same | Fresh analysis |
| New DAG run triggered | same | changes | Fresh analysis |
| Both | changes | changes | Fresh analysis |

Because the cache is stored in the metadata DB it is shared across all API server worker
processes and survives server restarts — unlike a per-process in-memory dict.

### 2.7 DAG Rewriter (§5.8)

**Not implemented.** The design doc's AST-based `DAGRewriter` that modifies live `.py` files is
intentionally excluded. The provider is read-only. The `dag_diff` field in the JSON report gives
the user enough information to apply the change manually. Automatic rewriting would require:
- Access to the DAG source file path (not available from the serialized DAG)
- A safe write-back mechanism with conflict detection
- User confirmation flow

This is deferred to a future iteration.

### 2.8 Airflow Plugin and UI Integration (§5.9)

The plugin registers under the `recommendations` category so multiple recommendation providers
can share one parent tab in the UI. The iframe URL includes `{DAG_ID}` which the Airflow frontend
substitutes at render time.

The UI page is a self-contained HTML + vanilla JS application (no external assets). It:
- Syncs dark/light theme with the parent Airflow window via `MutationObserver`
- Fetches `recommendations.json` via `fetch()` and renders the list client-side
- Lists edges sorted by verdict (`remove` first, then `uncertain`, then `keep`)
- Renders a score bar, signal-by-signal breakdown table, time-savings box, and DAG diff per edge
- Supports live search/filter

**UI rendering note:** the list pane (`#listMount`) and the detail pane are appended to the
document root *before* `renderList()` / `showDetail()` are called. References to internal
elements (`searchInput`, `listMount`) are captured once from `listPane`'s own subtree using
`listPane.querySelector(…)` rather than `document.getElementById(…)`. This is necessary
because `document.getElementById` only finds elements already attached to the document — calling
it on a detached element returns `null`, which caused the list to render blank on first load.

---

## 3. What Was Not Implemented (vs design doc)

| Design doc section | Status | Reason |
|---|---|---|
| §5.8 DAG Rewriter | Not implemented | Read-only provider; file path not available from serialized DAG |
| §5.2 Topological Level Scheduler | Not implemented | Covered by the Monte Carlo makespan comparison; no need for a separate scheduler |
| §6 Database tables (`uape_edge_analysis`, etc.) | Partially implemented | `uape_report_cache` table stores computed reports; no per-edge or per-run tables |
| §9 Async `POST /v1/analyze/{dag_id}` | Not implemented | Synchronous GET endpoint; acceptable for dev/advisory use |
| §11 Kubernetes sidecar / separate service | Not implemented | Runs in-process on the API server as a FastAPI sub-app |
| Dynamic XCom pull detection (§5.1.2) | Partial | Only literal string/list `task_ids` args detected in AST |
| Pearson timing correlation (§5.1.3) | Deviated | Uses gap-mean/std instead; more robust to schedule correlation |

---

## 4. File Structure

```
dev/uape-provider/
├── pyproject.toml                          # v0.2.0; deps: networkx, numpy, scipy
├── README.txt                              # User-facing install + usage guide
├── src/airflow/providers/uape/
│   ├── parallelization.py                  # Core engine: 4 signals, scoring, Monte Carlo
│   ├── cli/
│   │   ├── commands.py                     # airflow uape analyze / export
│   │   └── definition.py                   # CLI arg definitions
│   ├── web/
│   │   └── app.py                          # FastAPI sub-app + HTML UI
│   │                                       #   UapeReportCache — DB-backed result cache
│   │                                       #   _load_report()  — cache-aware entry point
│   │                                       #   _recommendations_ui_page() — self-contained HTML+JS
│   ├── plugins/
│   │   └── uape_plugin.py                  # AirflowPlugin registration
│   └── get_provider_info.py                # Provider metadata entry point
└── tests/
    ├── conftest.py                         # Dev-env bootstrap (stubs broken entry-points)
    └── test_parallelization.py             # 44 unit tests (pytest)
```

---

## 5. Key Data Structures

### `SignalResult`

```python
@dataclass
class SignalResult:
    name: str        # "asset_overlap" | "xcom_analysis" | "timing_correlation" | "transitive_reduction"
    passed: bool     # True = evidence of real dependency found
    weight: int      # 35 | 25 | 20 | 20
    explanation: str # Human-readable detail
    skipped: bool    # True = signal could not run (excluded from denominator)
```

### `EdgeScore`

```python
@dataclass
class EdgeScore:
    from_task: str
    to_task: str
    signals: list[SignalResult]
    confidence_score: int   # 0–100
    verdict: str            # "remove" | "uncertain" | "keep"
```

### `DurationProfile`

```python
@dataclass
class DurationProfile:
    task_id: str
    dist_name: str   # "lognorm" | "gamma" | "norm" | "empirical"
    params: tuple    # scipy distribution parameters (empty for empirical)
    mean: float      # seconds
    std: float
    p5: float        # 5th percentile (seconds)
    p50: float
    p95: float
    n_samples: int
```

### `SimulationResult`

```python
@dataclass
class SimulationResult:
    mean_savings_seconds: float
    p5_savings_seconds: float
    p50_savings_seconds: float
    p95_savings_seconds: float
    prob_improvement: float   # 0.0–1.0
    n_simulations: int        # default 10 000
```

---

## 6. JSON Report Format (schema 2.0)

```json
{
  "report_schema_version": "2.0",
  "generated_at_utc": "2025-04-18T10:00:00Z",
  "uape_provider_version": "0.2.0",
  "dag_id": "my_pipeline",
  "policy": "uncertainty_aware_v1",

  "graph_metrics": {
    "task_count": 6,
    "dependency_edge_count": 5,
    "redundant_edge_count": 1
  },

  "redundant_edges": [
    { "from_task": "fetch", "to_task": "report" }
  ],

  "summary": {
    "total_edges": 5,
    "remove_count": 1,
    "uncertain_count": 1,
    "keep_count": 3,
    "has_historical_data": true,
    "profiled_task_count": 4
  },

  "edge_analyses": [
    {
      "from_task": "train",
      "to_task": "report",
      "confidence_score": 18,
      "verdict": "remove",
      "signals": [
        { "name": "asset_overlap",        "passed": false, "weight": 35, "skipped": false,
          "score_contribution": 0,
          "explanation": "train outlets=[s3://models/]; report inlets=[s3://features/] — no overlap" },
        { "name": "xcom_analysis",        "passed": false, "weight": 25, "skipped": true,
          "score_contribution": 0,
          "explanation": "Task 'report' has no python_callable — skipping XCom code analysis" },
        { "name": "timing_correlation",   "passed": false, "weight": 20, "skipped": false,
          "score_contribution": 0,
          "explanation": "Mean gap: 22.4s, std: 14.3s — loose coupling (likely independent)" },
        { "name": "transitive_reduction", "passed": false, "weight": 20, "skipped": false,
          "score_contribution": 0,
          "explanation": "Edge is redundant — path train → validate → report already covers this" }
      ],
      "plain_explanation": "Recommend removing train → report (score: 18/100). 3 of 3 active signals found no evidence of a real dependency.",
      "suggested_fix": "Wire report after transform instead of train. This allows report to start in parallel with validate.",
      "time_savings": {
        "mean_savings_seconds": 687.4,
        "p5_savings_seconds": 312.1,
        "p50_savings_seconds": 695.8,
        "p95_savings_seconds": 1043.2,
        "prob_improvement": 0.947,
        "n_simulations": 10000
      },
      "dag_diff": "# Suggested change:\n# Remove: train >> report\n# Apply: Wire report after transform\n\n# Before:\n# train >> report\n\n# After (verify real parent first):\n# [wire report to its actual upstream dependency]\n"
    }
  ],

  "executive_summary": "DAG 'my_pipeline': 1 edge(s) recommended for removal (likely false dependencies); 1 edge(s) flagged for manual review out of 5 total edge(s)."
}
```

---

## 7. Scoring Examples

### All signals available, clear false dependency

| Signal | Weight | Passed | Contribution |
|--------|--------|--------|-------------|
| asset_overlap | 35 | ✗ | 0 |
| xcom_analysis | 25 | ✗ | 0 |
| timing_correlation | 20 | ✗ | 0 |
| transitive_reduction | 20 | ✗ | 0 |
| **Score** | | | **0 / 100 → remove** |

### Real dependency (asset overlap + XCom + tight timing)

| Signal | Weight | Passed | Contribution |
|--------|--------|--------|-------------|
| asset_overlap | 35 | ✓ | 35 |
| xcom_analysis | 25 | ✓ | 25 |
| timing_correlation | 20 | ✓ | 20 |
| transitive_reduction | 20 | ✓ | 20 |
| **Score** | | | **100 / 100 → keep** |

### Non-PythonOperator, no assets, direct edge (XCom skipped)

| Signal | Weight | Passed | Available | Contribution |
|--------|--------|--------|-----------|-------------|
| asset_overlap | 35 | ✗ | ✓ | 0 |
| xcom_analysis | 25 | — | ✗ skipped | 0 |
| timing_correlation | 20 | ✓ | ✓ | 20 |
| transitive_reduction | 20 | ✓ | ✓ | 20 |
| **Score** | 75 available | | | **40 / 75 → 53 → uncertain** |

---

## 8. CLI Usage Examples

```bash
# Human-readable analysis (all edges)
airflow uape analyze my_dag

# Show only edges recommended for removal
airflow uape analyze my_dag --verdict remove

# Skip Monte Carlo simulation (faster, no DB history needed)
airflow uape analyze my_dag --no-simulate

# Full JSON report
airflow uape export my_dag > report.json

# JSON, only uncertain edges
airflow uape analyze my_dag --format json --verdict uncertain
```

Sample text output:

```
DAG: my_pipeline  (policy: uncertainty_aware_v1)
Schema: 2.0 · generated 2025-04-18T10:00:00Z · provider 0.2.0

Graph: 6 tasks, 5 declared edges, 1 redundant (transitive)
Summary: 1 remove, 1 uncertain, 3 keep  [4 tasks profiled from history]

Redundant edges (already covered by longer paths — remove safely):
  fetch >> report

Edge analyses (5 shown):

  REMOVE     train >> report   score=18/100
    [✗] asset_overlap          train outlets=[s3://models/]; report inlets=[s3://features/] — no overlap
    [skip] xcom_analysis       Task 'report' has no python_callable — skipping XCom code analysis
    [✗] timing_correlation     Mean gap: 22.4s, std: 14.3s — loose coupling (likely independent)
    [✗] transitive_reduction   Edge is redundant — path train → validate → report already covers this
    → Suggestion: Wire report after transform instead of train.
    ⏱  Est. saving: 11.6 min median  (5.2–17.4 min range)  95% probability of improvement

  UNCERTAIN  validate >> notify   score=52/100
    ...

  KEEP       fetch >> transform   score=80/100
    ...
```

---

## 9. Installation and Setup

### In the dev environment (from repo root)

```bash
uv pip install -e dev/uape-provider
# Restart the API server so the plugin mount is active
```

### In Docker (via the example Dockerfile)

```bash
docker build -f example/Dockerfile -t my-airflow:uape-v2 .
```

The Dockerfile copies `dev/uape-provider` and installs it; `pyproject.toml` declares
`networkx`, `numpy`, and `scipy` as required dependencies, so they are installed automatically.

### Permissions required

The Recommendations tab is served via `/api/v2/plugins`. The authenticated user (or role) must
have **can read** on **Plugins**. Admin has this by default.

---

## 10. Running the Tests

```bash
uv run pytest dev/uape-provider/tests/test_parallelization.py -xvs
```

44 tests covering:
- Each signal in isolation (pass / fail / skip paths)
- Scoring engine (threshold behaviour, skipped-signal normalisation)
- Duration profiler (sample thresholds, distribution selection)
- Monte Carlo simulator (savings direction, field completeness)
- Full `analyze_dag_edges` pipeline (schema, metrics, redundant edge detection, summary counts)

The `tests/conftest.py` stubs Airflow's provider-discovery entry-point loading so that broken
or uninstalled provider packages in the dev environment do not abort test collection.

---

## 11. Known Limitations

| Limitation | Impact | Mitigation / Future work |
|---|---|---|
| No DAG Rewriter | Users apply changes manually | `dag_diff` field gives exact change; AST rewriter deferred |
| XCom analysis requires PythonOperator | 0 weight for Bash/SQL/etc operators | Other 3 signals still vote; score normalised over available signals |
| Timing signal needs ≥ 10 historical runs | New DAGs or rarely-run DAGs get timing skipped | Skipped signals excluded from denominator; doesn't penalise new DAGs |
| Dynamic XCom pulls not detected | `xcom_pull(task_ids=variable)` missed | Signal `skipped=False, passed=False`; explicitly documented |
| Synchronous on-demand analysis | Large DAGs (100+ tasks, 200+ edges) slow on first request | `--no-simulate` skips Monte Carlo; subsequent requests served from DB cache |
| Cache stores full JSON report per DAG | Report JSON can be large for very wide DAGs | Acceptable for dev use; consider compression for production promotion |
| Asset URI prefix matching only | Glob/regex path patterns not handled | Covers the dominant S3/GCS/ADLS prefix pattern; extend as needed |

---

## 12. Relationship to the Design Document

The design doc (`v2.md`) describes a production-grade system with async workers, a separate
database schema, Kubernetes deployment, and a full DAG rewriter. This implementation is a
**development provider** — a focused, in-process prototype of the core analytical pipeline:

| Design doc | This implementation |
|---|---|
| Async analysis service with job queue | Synchronous on-demand analysis in FastAPI endpoint |
| `uape_*` database tables | `uape_report_cache` table (auto-created); no per-edge tables |
| Kubernetes sidecar with resource limits | In-process sub-app on the API server |
| Full DAG Rewriter with AST manipulation | `dag_diff` field only (no file writes) |
| `POST /v1/analyze` → `GET /v1/recommend` | Single `GET /uape/dags/{id}/recommendations.json` |
| Separate deployment + Helm chart | `pip install dev/uape-provider` |

The four signals, the confidence scoring model, the Monte Carlo simulation, and the report
schema are faithful to the design. The implementation can be promoted to a production service
by adding a persistence layer, async workers, and the DAG rewriter without changing the
analytical core.
