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

These rules define v1 behavior.

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

   Examples of operators that default to opaque unless explicitly annotated:
   `BashOperator`, `PythonOperator` / `@task` with arbitrary user code, `DockerOperator`
   running opaque images, and similar "runs a script" patterns.

3. **Clear tasks (structure-only reasoning allowed)**  
   Operators whose **declared** inputs/outputs are the only contract Airflow needs
   (e.g., many SQL operators against declared connections, or tasks explicitly marked
   `structure_only=True` TBD) may participate in **parallelism surfacing** between
   siblings that are already independent in the graph — UAPE only **highlights** safe
   overlap, it does not invent new edges.

4. **Uncertainty = abstain**  
   If classification is unknown or mixed within a task group, **abstain**: no
   structural recommendation that would change ordering or introduce new concurrent
   edges across the uncertain region.

---

## Goals

1. **Independence analysis**: From the serialized DAG, compute sets of tasks that
   are **pairwise non-comparable** (no path between them) and thus **may** run in
   parallel under Airflow's normal scheduler — restricted to regions where all
   involved tasks are **not** opaque.

2. **Opaque vs clear classification**: Pluggable rules (operator type, DAG author
   tags, allow/deny lists) to label tasks opaque or clear; default conservative.

3. **Advisory outputs** (v1): CLI / UI / API that report "these tasks are already
   independent; your file over-serializes them" **only** when policy (1)–(4) pass;
   otherwise output **no change** or "manual review required."

4. **Optional metrics** (non-binding): Historical duration or pool wait histograms
   for **what-if** text ("if you refactor edges yourself, rough time saved") —
   clearly separated from **safety** recommendations.

5. **Observability**: For every recommendation, cite **graph proof** (which paths
   are absent). For abstentions, cite **opaque** or **unknown** classification.

6. **Integration touchpoints**: Read serialized DAG + TaskInstance metadata; no
   execution of user task code.

## Non-Goals

- Reordering tasks or running B before A when the DAG says `A >> B`
- Inferring independence from log correlation ("these tasks rarely interact")
- Replacing the Airflow scheduler; optional hints must not weaken declared semantics
- Parallelizing through an opaque task by speculating that scripts "probably" do not
  conflict

---

## Core Concepts

### 1. Graph of declared dependencies

Single source of truth: **Airflow's serialized dependency graph** (and dynamic map
edges where already resolved for a run). No parallel suggestion without a **proof**
of non-comparability in this graph among **clear** tasks only.

### 2. Opacity (opaque vs clear)

- **Opaque**: static analysis cannot bound side effects; **do not change order** or
  suggest new parallelism involving breaking serial chains that include them.
- **Clear**: bounded contract (or explicit author opt-in) so only declared deps matter
  for safety of sibling parallelism **reports**.

Exact taxonomy and opt-in API (`@task(structure_only=True)` or DAG-level YAML) are
implementation details to be agreed during design review.

### 3. What "uncertainty-aware" means in this AIP

| Situation | Engine behavior |
|---|---|
| Independent in graph, all clear | May recommend "safe to parallelize / you serialized unnecessarily" |
| Independent in graph but opaque sibling in scope | Abstain or scope recommendation to clear subgraph only |
| Dependent in graph | Never recommend parallel |
| Hidden dependency suspected (no graph edge) | Out of scope — **do not** parallelize; docs warn author |

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

### CLI (illustrative)

```bash
# List structurally independent task pairs (clear tasks only; proof in output)
airflow dag optimize independence-report my_dag_id --format text

# JSON with graph citations + opaque classification per task
airflow dag optimize export my_dag_id --format json
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

### Phase 1 — Independence + opaque classification

- Parse serialized DAG; compute topological levels and antichains of **clear** tasks.
- Default operator → opaque map; allowlist for **clear** builtins.
- Text/JSON report with proofs and abstentions.

### Phase 2 — API + UI

- REST + React panel aligned with policy (1)–(4).

### Phase 3 — Author annotations

- Explicit tags to mark tasks clear or opaque; team overrides in config.

### Phase 4 — Optional non-binding stats

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
