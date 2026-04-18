# AIP-UAP: DAG Optimizer — Uncertainty-Aware Parallelization Engine

**Author:** [Your Name]
**Status:** Draft
**Created:** 2026-04-11
**Category:** Core / Scheduler & DAG Execution
**Discussions-To:** https://github.com/apache/airflow/discussions

---

## Abstract

This AIP proposes an **Uncertainty-Aware Parallelization Engine (UAPE)** — a DAG
optimizer for Apache Airflow whose **primary contract** is conservative:

- **Parallelize only when independence is provable** from the **declared** DAG
  structure (serialized task dependencies). If two tasks have **no dependency path**
  between them in the graph Airflow already knows about, the scheduler may run them
  in parallel today; UAPE helps **surface** that fact, validate it against optional
  metadata, and avoid authors accidentally **serializing** work that could safely
  overlap.

- **When semantics are unclear, do not change order.** Tasks that are **opaque** to
  static reasoning — arbitrary scripts, shell commands, operators that may touch
  shared mutable state outside XCom/declared edges — are treated as **confusing**:
  UAPE **must not** recommend reordering, inserting parallelism across them, or
  batching that would alter relative ordering versus the authored DAG.

**Uncertainty** here means: *lack of proof of safety* defaults to **no change** to
execution semantics. Statistical models (durations, pools, tail latency) may still
inform **advisory** reporting (e.g., expected wall time if you refactor edges **yourself**)
but **must not** override the conservative rule above.

---

## Motivation

### Declared deps vs hidden deps

Airflow only sees **edges** you declare (`>>`, lists, task groups, mapped upstreams).
Many failures come from **hidden** dependencies inside a task body: a Bash script
that reads a file another task writes, a Python callable that hits a shared table
without using XCom or datasets. Parallelizing those because "the graph says OK"
would be wrong.

Teams also leave **false serialization** in place: `a >> b >> c` when `b` and `c`
do not depend on each other in reality — but the optimizer must only suggest
parallelism when the **author** can split the chain **without** breaking hidden
assumptions. For **opaque** middle tasks, the safe answer is: **keep the author's order**.

### What this AIP is not

It is **not** a stochastic scheduler that reorders tasks to minimize expected makespan.
It is **not** "run these in parallel because history says they're usually independent."
Independence must follow from **structure** (and optional explicit annotations),
not from correlation in logs.

---

## Conservative parallelization policy (normative)

These rules define the current behavior (policy `conservative_v2`).

1. **Structural independence (required for any parallel recommendation)**  
   Tasks *A* and *B* may be recommended to run in parallel **only if** the serialized
   DAG shows there is **no directed dependency** between them in either direction
   (neither is upstream of the other in Airflow's dependency graph). Equivalently:
   they may appear in the **same topological level** and scheduling them concurrently
   does not violate any declared edge.

2. **Opaque / confusing tasks (no order or parallelism changes)**  
   If a task is classified **opaque** (see below), UAPE:

   - does **not** recommend splitting or merging it with neighbors to expose parallelism;
   - does **not** recommend changing order relative to upstream/downstream tasks;
   - may still report duration stats **without** implying safe reordering.

   Operators not on the clear allowlist (see §3) are **opaque** by default: e.g.
   `DockerOperator`, cloud-provider operators, and any unknown or custom operator type.

3. **Clear tasks — tiered allowlist (structure-only reasoning allowed)**  
   The clear allowlist is split into three tiers. UAPE generates overlap hints only
   when **both** tasks in an independent pair are clear; hint confidence reflects the
   lower tier of the two.

   | Tier | Label | Confidence | Examples |
   |------|-------|------------|---------|
   | T1 | `t1_trivial` | `high` | `EmptyOperator`, `DummyOperator`, `LatestOnlyOperator` |
   | T2 | `t2_computation` | `medium_high` | `PythonOperator`, `BashOperator`, `TimeSensor`, `BranchPythonOperator`, `ShortCircuitOperator`, `TriggerDagRunOperator`, `ExternalTaskSensor`, … |
   | T3 | `t3_user_extended` | `medium` | Any operator type supplied by the caller (see below) |

   T2 operators such as `PythonOperator` and `BashOperator` are classified as clear
   because they have no *inherent* shared external state — but their actual side effects
   depend on user-supplied callables. The `medium_high` confidence and per-hint caveat
   remind authors to verify before relying on parallel execution.

   **User-extensibility (T3):** DAG authors and platform operators can promote additional
   operator types to the clear allowlist without modifying the package:

   - Set the `UAPE_EXTRA_CLEAR_OPERATOR_TYPES` environment variable (comma-separated)
     for a persistent process/container-level override.
   - Pass `--extra-clear-types Op1,Op2` to `airflow uape independence-report` or
     `airflow uape export` for a per-invocation override (suppresses env-var lookup).
   - Pass `extra_clear_types={"Op1"}` to `analyze_serialized_dag()` in Python.

   T3 types get `confidence: "medium"` to signal that UAPE has not analysed their
   semantics; the structural independence proof is identical to T1/T2.

4. **Uncertainty = abstain**  
   If classification is unknown or mixed within a task group, **abstain**: no
   structural recommendation that would change ordering or introduce new concurrent
   edges across the uncertain region. Independent pairs involving at least one opaque
   task appear under `abstained_parallel_hints` (reference only — not recommendations).

---

## Goals

1. **Independence analysis**: From the serialized DAG, compute sets of tasks that
   are **pairwise non-comparable** (no path between them) and thus **may** run in
   parallel under Airflow's normal scheduler — restricted to regions where all
   involved tasks are **not** opaque.

2. **Opaque vs clear classification with tiered confidence**: Operator types are
   assigned to T1 (trivial), T2 (computation), or T3 (user-extended) tiers.
   Unknown types default to opaque. Hint confidence reflects the lower tier of each
   pair: both T1 → `high`; T1+T2 or T2+T2 → `medium_high`; any T3 → `medium`.

3. **Advisory outputs**: CLI / UI / API that report "these tasks are already
   independent; your file over-serializes them" **only** when policy (1)–(4) pass;
   otherwise output **no change** or "manual review required."

4. **User-extensibility**: Platform operators and DAG authors can promote additional
   operator types to the clear allowlist (T3) without modifying the package, via an
   environment variable or CLI flag.

5. **Optional metrics** (non-binding): Historical duration or pool wait histograms
   for **what-if** text ("if you refactor edges yourself, rough time saved") —
   clearly separated from **safety** recommendations.

6. **Observability**: For every recommendation, cite **graph proof** (which paths
   are absent) and **tier + confidence**. For abstentions, cite **opaque** or
   **unknown** classification.

7. **Integration touchpoints**: Read serialized DAG + TaskInstance metadata; no
   execution of user task code.

## Non-Goals

- Reordering tasks or running B before A when the DAG says `A >> B`
- Inferring independence from log correlation ("these tasks rarely interact")
- Replacing the Airflow scheduler; optional hints must not weaken declared semantics
- Parallelizing through an opaque task by speculating that scripts "probably" do not
  conflict
- Automatically inferring hidden dependencies from task bodies (file I/O, shared DBs,
  undeclared XCom usage) — these remain the author's responsibility; each hint carries
  a caveat to this effect

---

## Core Concepts

### 1. Graph of declared dependencies

Single source of truth: **Airflow's serialized dependency graph** (and dynamic map
edges where already resolved for a run). No parallel suggestion without a **proof**
of non-comparability in this graph among **clear** tasks only.

### 2. Opacity (opaque vs clear) and tiers

- **Opaque**: static analysis cannot bound side effects; **do not change order** or
  suggest new parallelism involving breaking serial chains that include them.
  Examples: `DockerOperator`, cloud-provider operators, any unknown operator type.
- **Clear (T1 — trivial)**: operators with no external I/O (`EmptyOperator`,
  `DummyOperator`, `LatestOnlyOperator`); confidence `high`.
- **Clear (T2 — computation)**: common Airflow operators with no *inherent* shared
  external state (`PythonOperator`, `BashOperator`, `TimeSensor`,
  `BranchPythonOperator`, etc.); confidence `medium_high`. Their actual side effects
  depend on user callables; the caveat in each hint and the downgraded confidence
  communicate this.
- **Clear (T3 — user-extended)**: operator types explicitly promoted by the caller via
  the `UAPE_EXTRA_CLEAR_OPERATOR_TYPES` env var or `--extra-clear-types` CLI flag;
  confidence `medium`.

Hint confidence equals the lower tier's label: both T1 → `high`; T1+T2 or both T2 →
`medium_high`; any T3 → `medium`. Mapped operators are always opaque regardless of tier.

### 3. What "uncertainty-aware" means in this AIP

| Situation | Engine behavior |
|---|---|
| Independent in graph, both T1 clear | Recommend; confidence `high` |
| Independent in graph, both T2 clear | Recommend; confidence `medium_high` |
| Independent in graph, one or both T3 | Recommend; confidence `medium` |
| Independent in graph, at least one opaque | Abstain; record in `abstained_parallel_hints` (reference only) |
| Dependent in graph | Never recommend parallel |
| Hidden dependency (no graph edge) | Out of scope — **do not** parallelize; caveat in every hint warns author |

### 4. Optional secondary use: duration / pool reporting

Belief distributions over **duration** or queue wait may be attached to **advisory**
sections for capacity planning. They **must not** be used to justify parallelizing
tasks that are not structurally independent or that cross opaque barriers.

---

## Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│              Uncertainty-Aware Parallelization Engine (UAPE)             │
├─────────────────────────────────────────────────────────────────────────┤
│  Serialized DAG ──► Independence analyzer (topo levels, antichains)      │
│         │                    │                                             │
│         │                    ▼                                             │
│         │            Opaque / clear classifier                             │
│         │                    │                                             │
│         ▼                    ▼                                             │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ Policy: parallel recommendation IFF independent ∧ all involved    │   │
│  │         tasks clear; else abstain (preserve author order).         │   │
│  └──────────────────────────────┬───────────────────────────────────┘   │
│                                 │                                        │
│  Optional: TI stats ───────────►│──► Advisory API / CLI / UI              │
│  (durations, pools; no reorder) │    (proof + classification cited)      │
└─────────────────────────────────────────────────────────────────────────┘
```

**Phase 1** is **offline / advisory only**. Any future scheduler hints must preserve
declared dependencies and must not run opaque regions out of author order.

---

## User Experience

### CLI (current implementation)

```bash
# List structurally independent task pairs (clear tasks only; tiered confidence + proof)
airflow uape independence-report my_dag_id --format text

# Promote a custom operator type to the T3 clear tier for this run
airflow uape independence-report my_dag_id --extra-clear-types MyCustomOperator

# Promote permanently for a process/container
export UAPE_EXTRA_CLEAR_OPERATOR_TYPES="MyCustomOperator,AnotherOperator"
airflow uape independence-report my_dag_id

# Full JSON export (all classifications, proofs, hints, abstentions, tier breakdown)
airflow uape export my_dag_id --format json
```

### UI

- Highlight **levels** of the DAG where multiple **clear** tasks could run concurrently
  but the file forces unnecessary sequencing.
- **Gray out** or omit parallel hints for opaque chains with tooltip: "Order preserved
  — arbitrary script / unclear side effects."

### API

- `GET /api/v2/dags/{dag_id}/optimizer/recommendations` — returns recommendations
  each carrying `proof: { kind: "independence", paths: ... }` or `abstain_reason`.

---

## Data Model & Privacy

- Uses metadata and serialized DAG structure visible under existing RBAC.
- Optional TI aggregates for reporting only; same visibility rules as metrics today.

---

## Security Considerations

- No execution of user operators in the analyzer.
- Recommendations are **read-only hints**; must not introduce new execution paths
  that violate declared `>>` semantics.

---

## Backwards Compatibility

- Feature off by default; no behavior change.
- Advisory-only v1 does not alter scheduling.

---

## Implementation Plan

### Phase 1 — Independence + tiered clear classification ✓ (implemented)

- Parse serialized DAG; compute structurally independent pairs.
- Tiered allowlist: T1 (trivial), T2 (computation, 16 built-in operators), T3
  (user-extended via env var / CLI flag / Python argument).
- Tier-aware confidence: `high` (T1+T1), `medium_high` (T1+T2 or T2+T2), `medium`
  (any T3 involved).
- Text/JSON report with proofs, abstentions, tier labels, and per-hint confidence.
- `clear_operator_allowlist_tiers` and `clear_tier_counts` in graph metrics.
- Report schema `1.2`, policy `conservative_v2`.

### Phase 2 — API + UI ✓ (implemented)

- FastAPI sub-app mounted at `/uape`; plugin tabs on DAG / run / task / task-instance
  screens.
- UI shows tier badges (T1 green / T2 blue / T3 amber) and confidence labels per hint.
- Task opacity table includes a Tier column.

### Phase 3 — Formal author annotations (future)

- Explicit DAG/task-level tags to mark tasks clear or opaque; richer team overrides
  in Airflow config. The T3 env-var/flag mechanism is a lightweight precursor.

### Phase 4 — Optional non-binding stats (future)

- Attach duration histograms to reports; strict separation from safety section.

---

## Open Questions

1. Exact **opt-in** syntax for "this `@task` is structure-only" without burdening
   every DAG author.
2. How to treat **TaskGroups** that mix opaque and clear tasks.
3. **Dynamic task mapping**: independence among mapped instances vs structural proof
   at parse time vs run time.
4. Interaction with **datasets / assets** — treat as declared edges only.

---

## Relations to Other Work

- **Scheduler** — already runs independent tasks in parallel; UAPE **documents** and
  **guards** suggestions, it does not replace topo sort.
- **Dynamic Task Mapping** — independence among instances follows same proof rules.
- **Pipeline SLO** (ideas/AIP-03) — optional duration reporting only; no coupling to
  reordering policy.

---

## Success Metrics (proposal-level)

- Authors trust the tool because it **never** suggests parallelism across opaque script
  chains.
- Reduction in **accidental** serialization among **clear** independent tasks where
  teams accept refactors.
- Abstention rate and reasons are transparent in the UI.

---

## References

- Apache Airflow: [DAG structure](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html),
  [Dynamic Task Mapping](https://airflow.apache.org/docs/apache-airflow/stable/authoring-and-scheduling/dynamic-task-mapping.html)

---

## Document History

| Date | Author | Change |
|---|---|---|
| 2026-04-11 | [Your Name] | Initial draft |
| 2026-04-11 | [Your Name] | Refocus: graph-proven parallelism only; opaque tasks preserve order |
| 2026-04-17 | [Your Name] | Expand to tiered allowlist (T1/T2/T3); add user-extensibility via env var and CLI flag; update examples; bump schema to 1.2 / policy to conservative_v2 |
