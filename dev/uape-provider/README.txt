UAPE (Uncertainty-Aware Parallelization Engine) v2
===================================================

Analyses *declared* DAG edges for potential false dependencies using four independent signals,
scores each edge 0–100, and estimates time savings via Monte Carlo simulation over historical
task durations.

The ``AirflowPlugin`` (``/uape`` HTTP mount and UI tabs) is registered with the ``airflow.plugins``
entry point in ``pyproject.toml``.

Install into the same environment as Airflow (example from repo root):

  uv pip install -e dev/uape-provider

Then restart the API server so the plugin mount is active.

---

CLI Commands
------------

  airflow uape analyze <dag_id> [--format text|json] [--verdict remove|uncertain|keep|all] [--no-simulate]
    Analyse all declared edges and print results. Default: human-readable text, all verdicts,
    Monte Carlo simulation enabled (requires historical TaskInstance data in DB).

  airflow uape export <dag_id> [--format json] [--no-simulate]
    Print the full JSON analysis report (all edges, signals, simulation results).

---

HTTP API / UI Tab
-----------------

After installing and restarting the API server:

  GET {api_base}/uape/dags/{dag_id}/recommendations.json   — full JSON report
  GET {api_base}/uape/dags/{dag_id}/recommendations-ui     — HTML iframe page

The UI tab appears on DAG / Dag Run / Task / Task Instance pages under the **Recommendations**
category.

---

Four Signals (policy: uncertainty_aware_v1)
-------------------------------------------

Each declared edge is scored by four independent signals. A score < 40 triggers a "remove"
recommendation; 40–64 flags for manual review ("uncertain"); 65+ means "keep".

  Signal 1 — Asset overlap (weight 35 %)
    Checks declared ``inlets``/``outlets`` for shared asset URI prefixes.
    Exact-match and prefix-match (e.g. s3://bucket/prefix/ matches s3://bucket/prefix/file.parquet).

  Signal 2 — XCom code analysis (weight 25 %)
    AST-parses the downstream task's ``python_callable`` (if available) and looks for
    ``xcom_pull(task_ids='<upstream_id>')`` calls. Skipped for non-PythonOperator tasks.

  Signal 3 — Timing correlation (weight 20 %)
    Queries historical ``TaskInstance`` records and computes the gap between the upstream
    task's ``end_date`` and the downstream task's ``start_date``. A mean gap < 10 s with
    std < 5 s indicates tight coupling (likely a real dependency).
    Requires at least 10 successful historical runs; skipped without a DB session.

  Signal 4 — Transitive reduction (weight 20 %)
    Checks whether the edge survives ``networkx.transitive_reduction``. An edge removed
    by reduction has a longer path covering it — it is structurally redundant.

Skipped signals (no DB session, missing library, non-PythonOperator) are excluded from
the score denominator so they do not penalise real dependencies.

---

Monte Carlo Simulation
----------------------

For each "remove" recommendation with available historical duration data, the engine:

1. Fits a statistical distribution (lognormal/gamma/normal via AIC selection; falls back to
   empirical mean/std when scipy is unavailable) to each task's last 200 successful durations.
2. Samples 10 000 duration scenarios and computes makespan under the current DAG structure
   and the proposed structure (edge removed, tasks parallelised).
3. Reports P50 and P95 time savings and the probability of improvement.

---

Report Format (schema 2.0)
---------------------------

The JSON report includes:
  report_schema_version, generated_at_utc, uape_provider_version
  dag_id, policy
  graph_metrics   — task_count, dependency_edge_count, redundant_edge_count
  redundant_edges — list of {from_task, to_task} pairs removed by transitive reduction
  summary         — remove_count, uncertain_count, keep_count, has_historical_data, profiled_task_count
  edge_analyses   — one entry per declared edge:
      from_task, to_task, confidence_score (0–100), verdict (remove/uncertain/keep)
      signals       — list of {name, passed, weight, skipped, score_contribution, explanation}
      plain_explanation, suggested_fix, time_savings, dag_diff
  executive_summary

---

Dependencies
------------

  apache-airflow>=3.0.0
  networkx>=3.0          (transitive reduction signal)
  numpy>=1.24            (timing correlation, Monte Carlo)
  scipy>=1.10            (distribution fitting for Monte Carlo; falls back to empirical if absent)

---

Tests
-----

  uv run pytest dev/uape-provider/tests/test_parallelization.py -xvs

Requires a working Airflow dev environment where ``import airflow`` succeeds.

---

Design documentation: docs-design/aip-08/AIP-08-dag-optimizer-uncertainty-aware-parallelization-v2.md
Implementation note:  docs-design/aip-08/AIP-08-dag-optimizer-uncertainty-aware-parallelization-v1.md
