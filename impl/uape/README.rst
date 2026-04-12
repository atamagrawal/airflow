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

====================================
UAPE dev provider — implementation
====================================

This note documents the **Uncertainty-Aware Parallelization Engine (UAPE)** advisory
implementation shipped as an optional **development** provider. It is **not** part of
``apache-airflow`` core; it does not change scheduling.

Code location: ``dev/uape-provider/`` (distribution name ``apache-airflow-dev-uape``,
import path ``airflow.providers.uape``).

For broader product motivation and normative policy goals, see the design note in
``ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md`` (repository-local).


What it does
============

The provider adds read-only CLI commands that:

1. Load the **latest serialized DAG** for a ``dag_id`` from the metadata database
   (``SerializedDagModel``).
2. Build the **declared dependency graph** from each task's ``downstream_task_ids``.
3. Classify each task as **clear** or **opaque** using a **conservative** default.
4. List unordered task pairs that are **structurally independent** (no directed path
   in either direction in that graph).
5. Emit **overlap hints** only for independent pairs where **both** tasks are **clear**;
   otherwise record an **abstention** with a reason.

No user operator code, callbacks, or task bodies are executed. Only serialized metadata
is used.


Installation
============

From the repository root, install into the **same Python environment** as Airflow::

    uv pip install -e dev/uape-provider

After installation, Airflow's normal provider discovery registers the CLI group.


CLI reference
=============

All commands live under ``airflow uape``.

``airflow uape independence-report <dag_id> [--format text|json]``
    Prints a human-readable summary (default) or a JSON subset focused on:

    * ``clear_task_overlap_hints`` — independent pairs that pass the clear/clear filter
    * ``abstained_parallel_hints`` — independent pairs where at least one task is opaque

``airflow uape export <dag_id> [--format json]``
    Prints the full machine-readable report, including per-task classifications and the
    complete list of structurally independent pairs (with proofs).


Examples: how it behaves
========================

The samples below are **illustrative**: they show the kind of DAG shape and CLI output
you should expect. Task types such as ``BashOperator`` / ``PythonOperator`` appear only
as stand-ins for **opaque** operators under the current allowlist (everything except
``EmptyOperator``).


Prerequisite
------------

The DAG must already exist in **serialized DAG** storage (typical when the Dag Processor
has parsed the bundle and serialization is enabled). If not, the command exits with an
error asking you to ensure the DAG is serialized.


Example 1: EmptyOperator diamond to an opaque join
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Imagine a DAG (conceptually)::

        e1                 e2
    (EmptyOperator)  (EmptyOperator)
         \                /
          \              /
           \            /
            v          v
                 join
            (e.g. BashOperator)

* Declared edges: ``e1 >> join``, ``e2 >> join`` (no edge between ``e1`` and ``e2``).
* **Structural independence:** ``(e1, e2)`` — there is no directed path from ``e1`` to
  ``e2`` nor from ``e2`` to ``e1`` in the serialized graph, so this pair is reported with
  a proof of that fact.
* **Opacity:** ``EmptyOperator`` tasks are **clear**; ``BashOperator`` is **opaque**.
* **Outcome:** ``(e1, e2)`` appears under ``clear_task_overlap_hints`` with a caveat that
  hidden dependencies are still the author's responsibility. Pairs such as ``(e1,
  join)`` are **not** structurally independent (there is a path ``e1 -> join``), so they
  never appear as an independence pair.

**Commands** (after ``uv pip install -e dev/uape-provider``)::

    airflow uape independence-report my_diamond_dag
    airflow uape independence-report my_diamond_dag --format json
    airflow uape export my_diamond_dag --format json

**Illustrative text output** (shape only; wording may vary slightly)::

    DAG: my_diamond_dag (conservative_v1)
    Clear operator allowlist: EmptyOperator

    Advisory overlap hints (clear tasks only, declared graph):
      - 'e1' <~> 'e2': no directed path from 'e1' to 'e2' and none from 'e2' to 'e1' in the serialized dependency graph

**Illustrative JSON fragment** from ``independence-report --format json`` (abridged)::

    {
      "dag_id": "my_diamond_dag",
      "policy": "conservative_v1",
      "clear_operator_allowlist": ["EmptyOperator"],
      "clear_task_overlap_hints": [
        {
          "task_a": "e1",
          "task_b": "e2",
          "proof": {
            "kind": "no_directed_path_either_direction",
            "detail": "no directed path from 'e1' to 'e2' and none from 'e2' to 'e1' in the serialized dependency graph"
          },
          "recommendation": "advisory_parallel_overlap_by_declared_graph_only",
          "caveat": "Hidden dependencies are out of scope; confirm before changing task dependencies."
        }
      ],
      "abstained_parallel_hints": []
    }

Use ``airflow uape export`` to see the full structure, including ``task_classifications``
for every task and every ``structurally_independent_pairs`` entry (including pairs that
only qualify for abstentions).


Example 2: parallel opaque siblings (independence without a hint)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

        bash_a          bash_b
    (PythonOperator) (PythonOperator)
           \              /
            \            /
             v          v
                  merge

* ``(bash_a, bash_b)`` can still be **structurally independent** if there is no declared
  edge between them.
* Both tasks are **opaque** under the default policy.
* **Outcome:** that pair appears under ``abstained_parallel_hints`` with reasons such as
  ``task_type 'PythonOperator' is not on the clear allowlist``. There is **no** entry in
  ``clear_task_overlap_hints`` for that pair.

So the tool **documents** independence in the serialized graph while **withholding** a
"safe to refactor for parallelism" style hint unless both ends are on the allowlist.


Example 3: linear chain (no independent pairs)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

::

    start >> middle >> end

* Any pair involving tasks on the same chain has a directed path one way or the other
  (except comparing a task to itself, which the engine skips).
* **Outcome:** there are **no** unordered pairs of *distinct* tasks that are structurally
  independent; ``structurally_independent_pairs`` is empty and both hint lists are empty.


How it works internally
=======================

Data source
-----------

``airflow.cli.commands`` handlers (in ``airflow.providers.uape.cli.commands``) open a
metadata DB session, call ``SerializedDagModel.get(dag_id)``, and deserialize via
``row.dag`` (a ``SerializedDAG``). If no row exists, the command fails with a clear
error: the DAG must be present in serialization (scheduler / Dag Processor must have
persisted it).


Graph construction
------------------

``parallelization._downstream_adjacency`` builds a forward adjacency list: for every
task id ``t``, edges ``t -> u`` for each ``u`` in ``downstream_task_ids``.


Structural independence
-----------------------

For each unordered pair ``(a, b)`` of task ids, the engine precomputes downstream
reachability from every node (depth-first traversal). The pair is **structurally
independent** if ``b`` is not reachable from ``a`` **and** ``a`` is not reachable from
``b``. Each such pair gets a **proof** object describing that absence of paths in the
serialized graph.

This matches the conservative rule: overlap is only discussed when Airflow's **declared**
edges do not impose an ordering between the two tasks.


Opacity (clear vs opaque)
---------------------------

``parallelization._classify_task`` assigns:

* **Opaque** — mapped operators (``is_mapped``), or any operator whose ``task_type`` is
  not on the built-in allowlist.
* **Clear** — only types listed in ``_CLEAR_OPERATOR_TYPES`` (currently ``EmptyOperator``
  only).

Unknown or general-purpose operators default to **opaque** so the tool does not imply
that script-heavy tasks are safe to reorder or "parallelize" in an advisory sense.


Report payload
--------------

``analyze_serialized_dag`` returns a JSON-serializable dict including:

* ``policy`` — literal ``conservative_v1`` for this implementation revision
* ``clear_operator_allowlist`` — serialized names on the allowlist
* ``task_classifications`` — per-task ``opacity`` and ``opacity_reason``
* ``structurally_independent_pairs`` — all independent pairs with proofs
* ``clear_task_overlap_hints`` — filtered recommendations plus caveat text
* ``abstained_parallel_hints`` — independent pairs blocked by opacity with reasons

The CLI simply formats this structure (text or ``json`` module output).


Provider wiring
---------------

The package is a normal **Apache Airflow provider** distribution:

* ``pyproject.toml`` exposes ``[project.entry-points."apache_airflow_provider"]`` pointing
  at ``get_provider_info``.
* ``get_provider_info`` returns minimal metadata including a ``cli`` list that names
  ``airflow.providers.uape.cli.definition.get_uape_cli_commands``.
* That function returns a ``GroupCommand`` named ``uape`` with the action commands above.

Airflow's ``ProvidersManager`` merges these commands into the root CLI at startup.


Non-goals and limitations
=========================

* **No scheduler integration** — hints are not consumed by the scheduler; execution
  semantics remain exactly those of the authored DAG.
* **No hidden-dependency analysis** — file side effects, shared databases, and undeclared
  edges are out of scope; the caveat in overlap hints reminds authors to verify.
* **Narrow clear allowlist** — most real tasks are opaque for reporting purposes; expand
  the allowlist only with types whose contracts are genuinely bounded.
* **Dev-only packaging** — the distribution name is prefixed with ``dev`` to signal that
  this is experimental / local tooling, not a published production provider.


Related paths
=============

* Implementation: ``dev/uape-provider/src/airflow/providers/uape/``
* Install readme: ``dev/uape-provider/README.txt``
* Design discussion (local): ``ideas/AIP-08-dag-optimizer-uncertainty-aware-parallelization.md``
