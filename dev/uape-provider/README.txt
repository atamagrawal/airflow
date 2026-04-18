UAPE (Uncertainty-Aware Parallelization Engine) — advisory CLI with tiered clear-operator allowlist.

The ``AirflowPlugin`` (``/uape`` HTTP mount and UI tabs) is registered with the ``airflow.plugins``
entry point in ``pyproject.toml``. For Docker, use ``example/plugins/uape_plugin_loader.py`` copied
into ``$AIRFLOW_HOME/plugins/`` (see ``example/Dockerfile``) so discovery does not depend on entry points.

**FAB auth:** the UI loads plugin tabs from ``GET /api/v2/plugins``, which requires the **Plugins**
permission. If tabs are missing, grant your role **can read** on **Plugins** (Admin has it by default).

Restart the API server after install.

Install into the same environment as Airflow (example from repo root):

  uv pip install -e dev/uape-provider

Then:

  airflow uape independence-report <dag_id> [--format text|json] [--extra-clear-types Op1,Op2]
  airflow uape export <dag_id> [--format json] [--extra-clear-types Op1,Op2]

After restarting the API server:

* **Airflow UI:** on DAG / Dag Run / Task / Task Instance pages, this provider registers under the
  **Recommendations** category (the shared panel when the UI groups by ``category``). The view
  ``name`` is **Parallel overlap** so additional recommendation plugins can use their own names in
  the same panel. Each iframe loads ``/uape/dags/{dag_id}/recommendations-ui`` (same auth as the UI session).
* **HTTP / CLI:** same report as ``airflow uape export``:

  GET {api_base}/uape/dags/{dag_id}/recommendations.json

---

**Tiered clear-operator allowlist (policy: conservative_v2)**

Operators are classified into three tiers. Overlap hints are generated when *both* tasks in an
independent pair are on the clear allowlist; confidence reflects the lower tier of the two.

  Tier 1 (T1 · trivial) — confidence: high
    No external I/O or side effects.
    EmptyOperator, DummyOperator, LatestOnlyOperator

  Tier 2 (T2 · computation) — confidence: medium-high
    Common Airflow operators whose parallelism is safe by design (Python callables, Bash,
    time-based sensors, control-flow operators). No inherent shared external resources.
    PythonOperator, BranchPythonOperator, ShortCircuitOperator,
    PythonVirtualenvOperator, ExternalPythonOperator, PythonSensor,
    BashOperator, BashSensor,
    TimeSensor, TimeDeltaSensor, DateTimeSensor,
    BranchDateTimeOperator, BranchDayOfWeekOperator,
    TriggerDagRunOperator,
    ExternalTaskSensor, ExternalTaskMarker

  Tier 3 (T3 · user-extended) — confidence: medium
    Caller-supplied types. Two ways to add them:
    a) Environment variable (permanent for a process / container):
         export UAPE_EXTRA_CLEAR_OPERATOR_TYPES="MyCustomOperator,AnotherOperator"
    b) Per-invocation CLI flag (takes precedence, suppresses env-var lookup):
         airflow uape independence-report my_dag --extra-clear-types MyCustomOperator,AnotherOperator

    T3 operators are treated exactly like T1/T2 for structural independence detection, but hints
    involving them carry confidence="medium" as a reminder that UAPE hasn't analysed their semantics.

**Mixed-tier confidence:**
  Both T1    → high
  T1 + T2    → medium_high   (lower tier wins)
  Both T2    → medium_high
  any + T3   → medium        (lower tier wins)

---

**Report (schema 1.2)** includes ``report_schema_version``, ``generated_at_utc``, ``uape_provider_version``,
``dag_id``, ``policy``, ``clear_operator_allowlist`` (flat sorted list), ``clear_operator_allowlist_tiers``
(breakdown by T1/T2/T3), ``graph_metrics`` (with ``clear_tier_counts``), ``task_classifications``
(each entry has ``clear_tier`` when the task is clear), ``executive_summary``,
``clear_task_overlap_hints`` (each with ``task_a_clear_tier``, ``task_b_clear_tier``, and ``confidence``),
``abstained_parallel_hints`` (reference, not recommendations), and ``analysis_limits``.

Use your deployment's API base URL and the same authentication your API server expects for other
HTTP calls (for example bearer tokens in front of the API).

**Tests:** ``uv run pytest dev/uape-provider/tests/test_parallelization.py`` (requires a working Airflow
dev environment where ``import airflow`` succeeds).

This is a normal Airflow provider distribution (apache_airflow_provider entry point),
not a PLUGINS_FOLDER plugin. It does not change airflow-core.

Implementation documentation: impl/uape/README.rst

Optional background note in this repository: ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md
