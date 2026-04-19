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

# UAPE dev provider — v1 implementation (superseded)

> **Status: superseded.**  This document describes the v1 implementation (conservative structural
> independence analysis). It has been replaced by the v2 implementation described in
> `AIP-08-dag-optimizer-uncertainty-aware-parallelization-v2.md`.
>
> The v1 approach found task pairs that were *already* parallel by structure (no declared edge).
> Because Airflow already runs those pairs concurrently, v1 confirmed existing correct behaviour
> rather than detecting missed parallelism. The v2 implementation corrects this by analysing
> *declared edges* for false dependencies — the actual source of over-serialization.

---

This note documents the original **Uncertainty-Aware Parallelization Engine (UAPE)** advisory
implementation (v1) shipped as an optional **development** provider. It is **not** part of
``apache-airflow`` core; it does not change scheduling.

Code location: ``dev/uape-provider/`` (distribution name ``apache-airflow-providers-uape``,
import path ``airflow.providers.uape``).

For broader product motivation and normative policy goals, see the design note in
``ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md`` (repository-local).


# What it does


The provider adds read-only **CLI commands** and a **JSON HTTP API** (for third-party
applications) that:

1. Load the **latest serialized DAG** for a ``dag_id`` from the metadata database
   (``SerializedDagModel``).
2. Build the **declared dependency graph** from each task's ``downstream_task_ids``.
3. Classify each task as **clear** or **opaque** using the tiered allowlist (see below).
4. List unordered task pairs that are **structurally independent** (no directed path
   in either direction in that graph).
5. Emit **overlap hints** only for independent pairs where **both** tasks are **clear**;
   otherwise record an **abstention** with a reason.

No user operator code, callbacks, or task bodies are executed. Only serialized metadata
is used.


# Troubleshooting: no UI tab or ``404`` on ``/uape``


UAPE registers its ``AirflowPlugin`` through the **``airflow.plugins``** distribution entry point
(see ``pyproject.toml``), so it is loaded on API/web startup even when
``[core] lazy_discover_providers`` is ``True``. If tabs are still missing, confirm the package is
installed, restart the API server, and check logs for plugin import errors.

Older notes about setting ``lazy_discover_providers`` to ``False`` apply only to plugins that are
declared solely via ``get_provider_info`` → ``plugins`` (not used for UAPE anymore).


# Installation


From the repository root, install into the **same Python environment** as Airflow:

```

uv pip install -e dev/uape-provider

```

After installation, Airflow's normal provider discovery registers the CLI group.


# Tiered clear-operator allowlist


Policy ``conservative_v2`` classifies operators into three tiers. Overlap hints are generated
when **both** tasks in a structurally independent pair are clear; hint confidence reflects the
**lower** of the two tiers.

Tier 1 — trivial (confidence: ``high``)
   Operators with no external I/O or side effects. Independence is structurally certain.

   * ``EmptyOperator``
   * ``DummyOperator`` (legacy alias)
   * ``LatestOnlyOperator``

Tier 2 — computation (confidence: ``medium_high``)
   Common Airflow operators whose execution does not inherently share external resources
   with sibling instances. The advisory is still graph-structure-only.

   * Python-based: ``PythonOperator``, ``BranchPythonOperator``, ``ShortCircuitOperator``,
     ``PythonVirtualenvOperator``, ``ExternalPythonOperator``, ``PythonSensor``
   * Bash-based: ``BashOperator``, ``BashSensor``
   * Time-based: ``TimeSensor``, ``TimeDeltaSensor``, ``DateTimeSensor``
   * Control-flow: ``BranchDateTimeOperator``, ``BranchDayOfWeekOperator``
   * DAG-level: ``TriggerDagRunOperator``
   * Airflow-internal: ``ExternalTaskSensor``, ``ExternalTaskMarker``

Tier 3 — user-extended (confidence: ``medium``)
   Operator types supplied by the caller at invocation time. Treated identically to built-in
   tiers for independence detection; confidence is downgraded as a reminder that UAPE has
   not analysed their semantics.

   Two ways to extend:

   a. **Environment variable** (permanent for a process / container):

```

      export UAPE_EXTRA_CLEAR_OPERATOR_TYPES="MyCustomOperator,AnotherOperator"

```

   b. **CLI flag** (per-invocation; suppresses env-var lookup when provided):

```

      airflow uape independence-report my_dag --extra-clear-types MyCustomOperator,AnotherOperator
      airflow uape export my_dag --extra-clear-types MyCustomOperator,AnotherOperator

```

   You can also pass ``extra_clear_types=`` directly to ``analyze_serialized_dag()`` in Python.

**Mixed-tier confidence:**

| Tier pairing | Confidence |
| --- | --- |
| T1 + T1 | `high` |
| T1 + T2 | `medium_high` |
| T2 + T2 | `medium_high` |
| any + T3 | `medium` |

Mapped operators are **always opaque** regardless of tier.


# CLI reference


All commands live under ``airflow uape``.

``airflow uape independence-report <dag_id> [--format text|json] [--extra-clear-types ...]``
   Prints a human-readable summary (default) or a JSON subset focused on:

   * ``clear_task_overlap_hints`` — independent pairs that pass the clear/clear filter, with
     tier labels and confidence for each hint.
   * ``abstained_parallel_hints`` — independent pairs where at least one task is opaque.

``airflow uape export <dag_id> [--format json] [--extra-clear-types ...]``
   Prints the full machine-readable report, including per-task classifications (with
   ``clear_tier``), ``clear_operator_allowlist_tiers`` (T1/T2/T3 breakdown),
   ``graph_metrics`` (with ``clear_tier_counts``), and the complete list of structurally
   independent pairs with proofs.


# HTTP API for third-party applications


The distribution registers an **Airflow plugin** (``airflow.plugins`` entry point →
``UapeAdvisoryPlugin``) that mounts a small **FastAPI** sub-application on the API server at
``/uape`` (must not collide with reserved prefixes such as ``/api/v2``).

**Airflow UI:** the plugin declares ``external_view`` entries with ``destination`` set to
``dag``, ``dag_run``, ``task``, and ``task_instance`` (each with a distinct ``url_route``), so a
**Recommendations** tab appears on the main DAG screen, the Dag Run screen, the Task screen,
and the Task Instance screen. Each tab loads an iframe pointing at
``/uape/dags/{dag_id}/recommendations-ui``, which shows tier badges and confidence labels
for each hint alongside the full analysis-details panel.

Endpoints:

* ``GET /uape/dags/{dag_id}/recommendations.json`` — full JSON report, or ``404`` with
  ``{"error": "..."}`` when the serialized DAG is missing (same payload as ``airflow uape export``).
* ``GET /uape/dags/{dag_id}/recommendations-ui`` — HTML page for the UI plugin tab.

The JSON report uses ``report_schema_version`` (currently ``1.2``) and includes
``generated_at_utc``, ``uape_provider_version``, ``policy`` (``conservative_v2``),
``clear_operator_allowlist`` (flat sorted list), ``clear_operator_allowlist_tiers``
(per-tier breakdown), ``graph_metrics`` (including ``clear_tier_counts``),
``executive_summary``, ``analysis_limits``, ``task_classifications`` (each entry has
``clear_tier`` when clear), ``clear_task_overlap_hints`` (with ``task_a_clear_tier``,
``task_b_clear_tier``, ``confidence``, ``recommendation_summary``, ``recommendation_key``,
``severity``, ``caveat``, and ``suggested_next_steps``), and ``abstained_parallel_hints``
(reference rows only — task pair + ``abstain_reason``).

If ``[api] base_url`` is a subpath (for example ``https://host/airflow/``), prefix the path:
``{path_from_base_url}/uape/dags/...``.

Third-party apps should call this URL with the **same authentication** and transport rules
they already use for the Airflow API (for example JWT or session cookies in front of the
API server, depending on deployment).

Restart the **API server** after installing or upgrading this package so the mount is active.


# Examples: how it behaves


The samples below are **illustrative** and show the kind of DAG shape and CLI output
you should expect. They reflect policy ``conservative_v2``.


## Prerequisite


The DAG must already exist in **serialized DAG** storage (typical when the Dag Processor
has parsed the bundle and serialization is enabled). If not, the command exits with an
error asking you to ensure the DAG is serialized.


### Example 1: PythonOperator diamond to an opaque join


Imagine a DAG (conceptually):

```

    py1                py2
(PythonOperator) (PythonOperator)
     \                /
      \              /
       \            /
        v          v
             join
        (e.g. MyCustomOperator)

```

* Declared edges: ``py1 >> join``, ``py2 >> join`` (no edge between ``py1`` and ``py2``).
* **Structural independence:** ``(py1, py2)`` — no directed path in either direction.
* **Opacity:** ``PythonOperator`` is **T2-clear**; ``MyCustomOperator`` is **opaque** (not
  on the built-in allowlist; add it via ``--extra-clear-types`` or the env var to promote it).
* **Outcome:** ``(py1, py2)`` appears under ``clear_task_overlap_hints`` with
  ``confidence: "medium_high"`` (both T2). ``(py1, join)`` and ``(py2, join)`` are
  **not** structurally independent (there is a path from ``py1`` to ``join``).

**Commands** (after ``uv pip install -e dev/uape-provider``):

```

airflow uape independence-report my_diamond_dag
airflow uape independence-report my_diamond_dag --format json
airflow uape export my_diamond_dag --format json

```

**Illustrative text output** (shape only; wording may vary slightly):

```

DAG: my_diamond_dag (conservative_v2)
Clear operator allowlist: BashOperator, BashSensor, BranchDateTimeOperator, ...
  ↳ extend with --extra-clear-types or $UAPE_EXTRA_CLEAR_OPERATOR_TYPES

Advisory overlap hints (clear tasks only, declared graph):
  - 'py1' <~> 'py2' [medium_high  tiers: t2_computation/t2_computation]: no directed path from ...

```

**Illustrative JSON fragment** from ``independence-report --format json`` (abridged):

```

{
  "dag_id": "my_diamond_dag",
  "policy": "conservative_v2",
  "report_schema_version": "1.2",
  "clear_operator_allowlist_tiers": {
    "t1_trivial": ["DummyOperator", "EmptyOperator", "LatestOnlyOperator"],
    "t2_computation": ["BashOperator", "PythonOperator", ...],
    "t3_user_extended": []
  },
  "clear_task_overlap_hints": [
    {
      "task_a": "py1",
      "task_b": "py2",
      "task_a_clear_tier": "t2_computation",
      "task_b_clear_tier": "t2_computation",
      "confidence": "medium_high",
      "proof": {
        "kind": "no_directed_path_either_direction",
        "detail": "no directed path from 'py1' to 'py2' and none from 'py2' to 'py1' ..."
      },
      "recommendation": "advisory_parallel_overlap_by_declared_graph_only",
      "recommendation_key": "graph_independent_clear_allowlist_pair",
      "recommendation_summary": "py1 and py2 can run at the same time ...",
      "caveat": "Based on DAG structure only. If these tasks share files, databases ...",
      "suggested_next_steps": [...]
    }
  ],
  "abstained_parallel_hints": []
}

```

Use ``airflow uape export`` to see the full structure: ``graph_metrics`` (with
``clear_tier_counts``), ``task_classifications`` (each with ``clear_tier``),
``structurally_independent_pairs``, and ``abstained_parallel_hints`` (with totals if capped).


### Example 2: custom operator promoted to Tier 3


```

    op_a              op_b
(MyEtlOperator) (MyEtlOperator)
       \              /
        \            /
         v          v
              sink

```

* Both tasks are opaque by default (not in T1/T2).
* Promote them for this run:

```

  airflow uape independence-report my_dag --extra-clear-types MyEtlOperator

```

* **Outcome:** ``(op_a, op_b)`` now appears in ``clear_task_overlap_hints`` with
  ``confidence: "medium"`` and ``clear_tier: "t3_user_extended"`` on both sides.


### Example 3: parallel opaque siblings (independence without a hint)


```

    op_a              op_b
(UnknownOperator) (UnknownOperator)
       \              /
        \            /
         v          v
              merge

```

* ``(op_a, op_b)`` can be **structurally independent** if there is no declared edge.
* Both tasks are **opaque** (not on any tier).
* **Outcome:** the pair appears under ``abstained_parallel_hints`` with reason such as
  ``task_type 'UnknownOperator' is not on the clear allowlist (conservative default)``.
  There is **no** entry in ``clear_task_overlap_hints`` for that pair.

The tool **documents** independence in the serialized graph while **withholding** a hint
unless both ends are on the allowlist.


### Example 4: linear chain (no independent pairs)


```

start >> middle >> end

```

* Any pair has a directed path one way or the other.
* **Outcome:** ``structurally_independent_pairs`` is empty and both hint lists are empty.


# How it works internally


## Data source


``airflow.cli.commands`` handlers (in ``airflow.providers.uape.cli.commands``) open a
metadata DB session, call ``SerializedDagModel.get(dag_id)``, and deserialize via
``row.dag`` (a ``SerializedDAG``). If no row exists, the command fails with a clear
error: the DAG must be present in serialization (scheduler / Dag Processor must have
persisted it).


## Graph construction


``parallelization._downstream_adjacency`` builds a forward adjacency list: for every
task id ``t``, edges ``t -> u`` for each ``u`` in ``downstream_task_ids``.


## Structural independence


For each unordered pair ``(a, b)`` of task ids, the engine precomputes downstream
reachability from every node (depth-first traversal). The pair is **structurally
independent** if ``b`` is not reachable from ``a`` **and** ``a`` is not reachable from
``b``. Each such pair gets a **proof** object describing the absence of paths.

This matches the conservative rule: overlap is only discussed when Airflow's **declared**
edges do not impose an ordering between the two tasks.


## Opacity (clear vs opaque) and tiers


``parallelization._classify_task`` assigns one of three clear tiers or opaque:

* **T1 (trivial)** — ``EmptyOperator``, ``DummyOperator``, ``LatestOnlyOperator``; confidence ``high``.
* **T2 (computation)** — common Python, Bash, time-based, control-flow, and DAG-level
  operators; confidence ``medium_high``.
* **T3 (user-extended)** — types supplied by the caller via ``extra_clear_types`` or the
  ``UAPE_EXTRA_CLEAR_OPERATOR_TYPES`` env var; confidence ``medium``.
* **Opaque** — mapped operators (``is_mapped``), or any operator not in T1/T2/T3.

Unknown or general-purpose operators default to **opaque** so the tool does not imply
that novel or script-heavy tasks are safe to reorder or parallelize without explicit opt-in.


## Report payload


``analyze_serialized_dag`` returns a JSON-serializable dict including:

* ``policy`` — literal ``conservative_v2`` for this implementation revision
* ``report_schema_version`` — ``"1.2"``
* ``clear_operator_allowlist`` — flat sorted list of all clear types (T1 ∪ T2 ∪ T3)
* ``clear_operator_allowlist_tiers`` — dict with keys ``t1_trivial``, ``t2_computation``,
  ``t3_user_extended``, each listing the operator types in that tier
* ``graph_metrics`` — includes ``clear_tier_counts`` breaking down clear tasks by tier
* ``task_classifications`` — per-task ``opacity``, ``opacity_reason``, and ``clear_tier``
  (present only for clear tasks)
* ``structurally_independent_pairs`` — all independent pairs with proofs
* ``clear_task_overlap_hints`` — hints with ``task_a_clear_tier``, ``task_b_clear_tier``,
  ``confidence``, ``recommendation_summary``, ``caveat``, ``suggested_next_steps``
* ``abstained_parallel_hints`` — independent pairs blocked by opacity, with reasons

The CLI formats this structure as text or JSON. The HTTP API returns the same dict.


## Provider wiring


The package is a normal **Apache Airflow provider** distribution:

* ``pyproject.toml`` exposes ``[project.entry-points."apache_airflow_provider"]`` pointing
  at ``get_provider_info`` (CLI metadata), and ``[project.entry-points."airflow.plugins"]`` pointing
  at ``UapeAdvisoryPlugin`` (FastAPI mount and UI ``external_views``).
* ``get_provider_info`` returns metadata including a ``cli`` list naming
  ``airflow.providers.uape.cli.definition.get_uape_cli_commands`` (the ``airflow uape`` group).

``plugins_manager`` loads entry-point plugins on API/web startup so ``/uape`` and UI tabs are
available without disabling ``lazy_discover_providers``.


# Non-goals and limitations


* **No scheduler integration** — hints are not consumed by the scheduler; execution
  semantics remain exactly those of the authored DAG.
* **No hidden-dependency analysis** — file side effects, shared databases, and undeclared
  edges are out of scope; the caveat in overlap hints reminds authors to verify.
* **T2 operators are still advisory** — even though ``PythonOperator`` or ``BashOperator``
  are on the clear allowlist, their *actual* side effects depend on user-supplied callables.
  The hint's caveat and ``confidence: "medium_high"`` (rather than ``"high"``) reflect this.
* **In-repo packaging** — lives under ``dev/uape-provider/`` as local/experimental tooling, not a
  published Apache community provider.
* **Airflow UI** — a plugin tab (iframe) is registered via ``external_views``; there is no fork of
  the core React bundle. CLI and JSON HTTP remain available.


# Related paths


* Implementation: ``dev/uape-provider/src/airflow/providers/uape/``
* Install readme: ``dev/uape-provider/README.txt``
* Design discussion (local): ``ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md``
