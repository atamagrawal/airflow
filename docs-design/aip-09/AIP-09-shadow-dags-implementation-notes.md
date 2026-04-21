# AIP-09: Shadow DAGs — Implementation Notes

**Status:** Implemented (airflow-core + task-sdk)  
**Design reference:** `docs-ideas/AIP-09-shadow-dags.md`  
**Target:** Airflow 3.2.0+

### Revision history

| Date | Change |
|------|--------|
| 2026-04-20 | Initial implementation — all 8 components shipped |

---

## 1. What Was Built

The Shadow DAGs feature (AIP-09) is implemented as a set of changes across `airflow-core` and `task-sdk`. It ships five surfaces:

| Surface | Entry point |
|---------|-------------|
| ORM model + migration | `airflow.models.shadow_dag` · migration `0110_3_2_0_add_shadow_dag_table` |
| Shadow package | `airflow.shadow` (sink_proxy, comparison, lifecycle) |
| `@shadow_dag` decorator | `airflow.sdk.definitions.shadow` / `from airflow.sdk import shadow_dag` |
| CLI | `airflow shadow create/list/report/promote/discard` |
| REST API | `GET/POST/DELETE /api/v2/shadow-dags/…` |
| Scheduler integration | `_create_shadow_dag_runs` + `_cleanup_expired_shadows` |
| React UI | Shadow Reports tab + Shadow Lane in Grid view |

---

## 2. File Map

```
airflow-core/src/airflow/
├── models/
│   └── shadow_dag.py               ← ShadowDag ORM model, ShadowDagStatus enum
├── migrations/versions/
│   └── 0110_3_2_0_add_shadow_dag_table.py  ← Alembic migration (rev f4a8c9e2b1d7)
├── shadow/
│   ├── __init__.py                 ← Package exports
│   ├── sink_proxy.py               ← ShadowContext, SinkProxy ABC, LocalFileSinkProxy
│   ├── comparison.py               ← ComparisonEngine, ComparisonReport, Verdict
│   └── lifecycle.py                ← ShadowDagService (create/get/list/transition/promote/discard/cleanup)
├── cli/
│   ├── cli_config.py               ← SHADOW_COMMANDS group + ARG_* definitions (modified)
│   └── commands/shadow_command.py  ← shadow_create/list/report/promote/discard handlers
├── api_fastapi/core_api/
│   ├── datamodels/shadow_dags.py   ← Pydantic request/response models
│   └── routes/public/
│       ├── __init__.py             ← authenticated_router.include_router(shadow_dags_router) (modified)
│       └── shadow_dags.py          ← FastAPI router with all endpoints
├── jobs/
│   └── scheduler_job_runner.py     ← _create_shadow_dag_runs, _cleanup_expired_shadows (modified)
└── dag_processing/
    └── collection.py               ← _auto_register_shadow_dags (modified)

task-sdk/src/airflow/sdk/
├── __init__.py                     ← "shadow_dag" exported (modified)
└── definitions/shadow.py           ← @shadow_dag decorator, SHADOW_TAG_PREFIX, decode_shadow_tag

airflow-core/src/airflow/ui/src/
├── pages/Dag/ShadowReports/
│   ├── ShadowReports.tsx           ← Shadow Reports tab page
│   └── index.ts
├── layouts/Details/Grid/
│   └── ShadowLane.tsx              ← Amber-bordered shadow state lane in Grid
├── pages/Dag/Dag.tsx               ← "Shadow Reports" tab added (modified)
├── layouts/Details/Grid/Bar.tsx    ← <ShadowLane> rendered per run column (modified)
└── router.tsx                      ← shadow_reports route added (modified)
```

### Test files

```
airflow-core/tests/unit/
├── shadow/
│   ├── test_sink_proxy.py
│   ├── test_comparison.py
│   └── test_lifecycle.py
├── models/test_shadow_dag.py
├── cli/commands/test_shadow_command.py
└── api_fastapi/core_api/routes/public/test_shadow_dags.py
```

---

## 3. Database Layer

### Model: `ShadowDag` (`shadow_dag` table)

```python
class ShadowDag(Base):
    __tablename__ = "shadow_dag"

    shadow_id: Mapped[str]               # PK — "shd_{dag_id}_{date}"
    production_dag_id: Mapped[str]
    candidate_dag_id: Mapped[str]
    status: Mapped[str]                  # ShadowDagStatus.value
    ttl_days: Mapped[int]
    divergence_alert_pct: Mapped[float]
    notify: Mapped[str | None]
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]
    last_comparison_json: Mapped[str | None]   # JSON-encoded ComparisonReport
```

`last_comparison_json` stores the full comparison report inline rather than a foreign key to a separate table. This trades normalisation for simplicity — the latest report is the most useful, and the JSON can be re-parsed on demand by the API and CLI.

### `ShadowDagStatus` enum

```
REGISTERED → ACTIVE → REVIEW → PROMOTED → CLEANED_UP
                    ↘ DISCARDED → CLEANED_UP
```

Valid transitions are enforced by `_VALID_TRANSITIONS` dict and `can_transition_to()`. Attempting an illegal transition raises `InvalidShadowTransition`.

### Migration

Revision `f4a8c9e2b1d7`, chained from `1d6611b6ab7c` (bundle_name on callback).  
No ORM imports — raw `op.create_table` with `sa.Column` per project convention.

Two indexes are created:
- `idx_shadow_dag_production_dag_id` — for fast lookup by production DAG in the scheduler
- `idx_shadow_dag_status` — for TTL cleanup queries

---

## 4. Shadow Package

### `sink_proxy.py`

**`ShadowContext`** is a frozen dataclass carrying runtime metadata for a shadow run:

```python
@dataclass(frozen=True)
class ShadowContext:
    shadow_id: str
    production_dag_id: str
    run_id: str
    sink_root: Path   # AIRFLOW_HOME/shadow/<shadow_id>/<run_id>/
```

`ShadowContext.from_env()` reads `AIRFLOW_SHADOW_RUN_ID` / `AIRFLOW_SHADOW_DAGRUN_ID` / `AIRFLOW_SHADOW_PROD_DAG_ID` from the worker environment and returns `None` when outside a shadow run.

**`LocalFileSinkProxy`** (the only concrete implementation in this release):

- Wraps the operator's `_pre_execute_hook` to inject `shadow_output_path` and `shadow_sink_root` into the task context
- Creates `<sink_root>/<task_id>/output.jsonl` for each task's output
- Does **not** mutate the operator class; only the instance passed to `wrap()`

Cloud sink proxies (BigQuery, GCS, Postgres) are deferred to Phase 2 / provider packages following the same `SinkProxy` ABC interface.

### `comparison.py`

`ComparisonEngine.compare()` workflow:

1. Read shadow output rows via `_read_shadow_rows()` — recursively finds all `output.jsonl` under `sink_root`
2. Read production output rows via `_read_prod_rows()` — same pattern from `prod_sink_root`
3. Compute `row_count_delta_pct = (shadow - prod) / prod * 100`
4. Detect schema divergence (`added` / `removed` columns)
5. Detect per-column value divergence (null rates, min/max/mean)
6. Collect up to 100 differing rows as `sample_diff_rows`
7. Call `_determine_verdict()`:
   - `DIVERGED` if schema diffs present or `|delta_pct| > alert_threshold * 100`
   - `WITHIN_THRESHOLD` if `|delta_pct| > 0`
   - `MATCH` otherwise
   - `SHADOW_FAILED` on any read exception

Any exception during data reading is caught; the returned `ComparisonReport` has `verdict=SHADOW_FAILED` and `error=<message>` so failures are observable without crashing the scheduler.

### `lifecycle.py`

**`ShadowDagService`** — all public methods accept a `session` argument and do **not** call `session.commit()`. Callers own the transaction boundary.

Key design decisions:

- `create()` is idempotent: calling it with the same `production_dag_id` on the same calendar date returns the existing record without error
- `shadow_id` format: `shd_{prod_dag_id[:40]}_{YYYYMMDD}` — human-readable and date-keyed
- TTL is capped at 14 days (enforced by `_parse_ttl()`); minimum is 1 day
- `cleanup_expired()` is called on every scheduler heartbeat; it transitions `REVIEW` and `DISCARDED` records whose `expires_at` is in the past to `CLEANED_UP`

**Stats metrics emitted:**

| Metric | When |
|--------|------|
| `shadow.created` | New shadow registered |
| `shadow.transition` | Any status change |
| `shadow.run.verdict` | After comparison report recorded |
| `shadow.row_delta_pct` | After comparison report recorded |
| `shadow.cleaned_up_count` | Batch cleanup during heartbeat |

---

## 5. `@shadow_dag` Decorator

### Mechanism

The decorator encodes shadow config as a DAG tag using a `__shadow__:<json>` prefix:

```python
shadow_tag = "__shadow__:" + json.dumps({
    "shadows": "etl.orders_daily",
    "ttl": "7d",
    "divergence_alert": 0.05,
    "notify": None,
})
```

When the wrapped `@dag` factory is called at module level, the wrapper adds this tag to `dag_obj.tags`. The tag then flows through DAG serialisation into `LazyDeserializedDAG.tags`.

### Auto-registration hook

`collection.py::_auto_register_shadow_dags()` is called at the end of `update_dag_parsing_results_in_db()` (i.e. whenever the DAG Manager processes a parse result). It:

1. Iterates `dags` (Collection of `LazyDeserializedDAG`)
2. For each tag that starts with `__shadow__:`, decodes the JSON
3. Calls `ShadowDagService.create()` for the (production_dag_id, candidate dag_id) pair
4. Any exception is caught and logged — shadow registration failure never blocks DAG persistence

This means the `@shadow_dag` decorator never touches the database and is safe to import in the DAG parsing subprocess, which does not have ORM access.

### Usage

```python
from airflow.sdk import dag, shadow_dag

@shadow_dag(
    shadows="etl.orders_daily",
    ttl="7d",
    divergence_alert=0.05,
    notify="data-eng-oncall@company.com",
)
@dag(schedule="@daily", catchup=False)
def orders_daily_v2():
    ...

orders_daily_v2()   # Instantiates the DAG; shadow tag is added
```

---

## 6. CLI

### Commands

```bash
airflow shadow create \
  --production-dag   <dag_id> \
  --candidate-dag-id <dag_id> \
  --ttl              7d \
  --divergence-alert 0.05 \
  --notify           <email>

airflow shadow list [--status <status>] [--output table|json|yaml]

airflow shadow report --shadow-id <id>

airflow shadow promote --shadow-id <id>

airflow shadow discard --shadow-id <id> [--yes]
```

All commands use `create_session()` and call into `ShadowDagService`. Output is rendered via `AirflowConsole` (rich-based).

### Registration in `cli_config.py`

`SHADOW_COMMANDS` tuple is defined as a sequence of `ActionCommand` instances and registered as:

```python
GroupCommand(name="shadow", help="Manage Shadow DAG experiments (AIP-09)", subcommands=SHADOW_COMMANDS)
```

appended to `core_commands`.

New `Arg` definitions prefixed `ARG_SHADOW_*` are added in the `# shadow` section.

---

## 7. REST API

Base path: `/api/v2/shadow-dags`  
Auth: Standard Airflow JWT (via `authenticated_router`).

### Endpoints

| Method | Path | Handler | Status codes |
|--------|------|---------|--------------|
| `GET` | `/shadow-dags` | `list_shadow_dags` | 200, 400 |
| `POST` | `/shadow-dags` | `create_shadow_dag` | 201, 400 |
| `GET` | `/shadow-dags/{shadow_id}` | `get_shadow_dag` | 200, 404 |
| `DELETE` | `/shadow-dags/{shadow_id}` | `discard_shadow_dag` | 204, 400, 404 |
| `POST` | `/shadow-dags/{shadow_id}/promote` | `promote_shadow_dag` | 200, 400, 404 |
| `GET` | `/shadow-dags/{shadow_id}/reports/latest` | `get_latest_report` | 200, 404, 500 |

### Pydantic models

```python
class ShadowDagCreateBody(StrictBaseModel):
    production_dag_id: str
    candidate_dag_id: str
    ttl: str = "7d"
    divergence_alert: float  # 0.0–1.0
    notify: str | None

class ShadowDagResponse(BaseModel): ...        # serialised ShadowDag record
class ShadowDagCollectionResponse(BaseModel):  # list + total_entries
    shadow_dags: list[ShadowDagResponse]
    total_entries: int

class ComparisonReportResponse(BaseModel): ... # serialised ComparisonReport
```

`ShadowDagResponse` is constructed from the ORM model via `_shadow_to_response()` — a plain function rather than a Pydantic `from_orm` call to keep the mapping explicit and avoid accidental lazy-load issues.

---

## 8. Scheduler Integration

### `_create_shadow_dag_runs(production_dag_runs, session)`

Called inside `_create_dagruns_for_dags()` **after** production DagRuns are added to the session but **before** `guard.commit()`, ensuring shadow and production runs are committed atomically.

```
session.new → filter DagRun instances → query active shadows → create shadow DagRun
```

Shadow DagRun `conf` carries:

```json
{
  "__shadow_run__": true,
  "__shadow_id__": "shd_orders_daily_20260420",
  "__prod_run_id__": "scheduled__2026-04-20T00:00:00+00:00",
  "__prod_dag_id__": "orders_daily"
}
```

Any failure inside `_create_shadow_dag_runs` is caught at the outermost try/except and logged. Production DagRun creation is **never** affected.

### `_cleanup_expired_shadows(session)`

Called on every `heartbeat_callback`. Delegates to `ShadowDagService.cleanup_expired()`. Any exception is caught and logged.

### Worker queue isolation

Shadow DagRuns are created with `run_type=DagRunType.MANUAL`. To route them to a dedicated worker queue, set `[operators] default_queue = shadow` in Airflow config for workers running shadow tasks, or use a queue-aware executor configuration. The `run_id` prefix `shadow__` is sufficient for custom routing rules in most executor setups.

---

## 9. React UI

### Shadow Reports Tab (`pages/Dag/ShadowReports/ShadowReports.tsx`)

Fetches `GET /api/v2/shadow-dags?production_dag_id=<dagId>` via `useQuery` (TanStack Query). For each shadow record, a nested `useQuery` fetches the latest comparison report from `/api/v2/shadow-dags/<id>/reports/latest`.

Displays a `Table` with columns: shadow ID, candidate DAG, status badge, latest verdict badge, row delta %, shadow/prod row counts, expiry date.

Empty-state message includes the CLI command to create a shadow experiment.

### Shadow Lane (`layouts/Details/Grid/ShadowLane.tsx`)

Rendered inside `Bar.tsx` below each production run column. Fetches active shadow metadata and latest report verdict from the API. Maps verdicts to Chakra color palettes:

| Verdict | Color |
|---------|-------|
| `MATCH` | `success` (green) |
| `WITHIN_THRESHOLD` | `yellow` |
| `DIVERGED` | `red` |
| `SHADOW_FAILED` | `gray` |

The lane has a 2px `orange.200` top border to visually separate it from the production run bar, matching the AIP-09 spec ("distinct amber border").

### Wiring

- `Dag.tsx` — `{ icon: <TbShadow />, label: "Shadow Reports", value: "shadow_reports" }` appended to `tabs`
- `router.tsx` — `{ element: <ShadowReports />, path: "shadow_reports" }` added inside the `dags/:dagId` children
- `Bar.tsx` — `<ShadowLane productionRunId={run.run_id} />` rendered below the `GridButton`

---

## 10. Known Limitations & Future Work

| Limitation | Notes |
|------------|-------|
| **Local sink only** | `LocalFileSinkProxy` writes JSON-lines to `$AIRFLOW_HOME/shadow/`. BigQuery, GCS, and Postgres proxies are deferred to Phase 2. |
| **No production output capture** | `ComparisonEngine._read_prod_rows()` returns `[]` when `prod_sink_root=None` (local mode). Full comparison requires production output routing, which needs provider-level work. |
| **Shadow run scheduling is best-effort** | Shadow DagRuns are created from `session.new` which may miss DagRuns created through other code paths (e.g. asset-triggered runs). A follow-up should hook into `_create_dag_runs_asset_triggered` as well. |
| **No per-task shadow config** | AIP-09 §3 explicitly marks per-task shadowing as out of scope. The entire DAG is shadowed. |
| **No shadow promotion automation** | `promote` marks the status as `PROMOTED` and logs a message. Actual deployment (PR creation, CI trigger) is deferred. |
| **Worker queue isolation requires manual config** | There is no automatic queue assignment. Teams must configure executor routing on `shadow__`-prefixed run IDs or use a dedicated queue name. |
| **`TbShadow` icon** | `react-icons/tb` must be available in the project's dependency graph. If the icon is not present, replace with any other icon from an already-imported set. |

---

## 11. Testing

Tests live in `airflow-core/tests/unit/`:

| File | What it covers |
|------|---------------|
| `shadow/test_sink_proxy.py` | `ShadowContext.from_env`, `LocalFileSinkProxy.wrap`, hook injection, output path resolution |
| `shadow/test_comparison.py` | JSONL loading, schema divergence detection, verdict determination, `ComparisonEngine.compare` (match, diverged, read error), `ComparisonReport` round-trip |
| `shadow/test_lifecycle.py` | `_parse_ttl`, `ShadowDagService` create/get/list/transition/promote/discard/cleanup (all mock-session, no DB required), `ShadowDagStatus.can_transition_to` matrix |
| `models/test_shadow_dag.py` | ORM model properties (`status_enum`, `is_active`, `is_terminal`), repr, exception messages |
| `cli/commands/test_shadow_command.py` | All 5 CLI handler functions (mock session + service) |
| `api_fastapi/…/test_shadow_dags.py` | All REST endpoints via `test_client` (mock service layer) |

All lifecycle tests mock `airflow.shadow.lifecycle.Stats` to avoid StatsD side-effects in CI.

---

*AIP-09 · Shadow DAGs · Implementation Notes · 2026-04-20 · Data Infrastructure*
