# AIP-09: Shadow DAGs — Docker Deployment Guide

**Status:** Implemented  
**Image:** `docs-example/Dockerfile` (extends `apache/airflow:3.2.0`)  
**Audience:** Developers who want to run the full Shadow DAGs feature locally or in a staging environment using the example Docker image.

---

## Overview

The AIP-09 Shadow DAGs feature is implemented as changes to `airflow-core` and `task-sdk` (not a provider package). Because `apache/airflow` is installed from PyPI inside the base image, the new and modified files must be **patched into site-packages at image build time** rather than `pip install`-ed.

The `docs-example/Dockerfile` handles this with two mechanisms:

| Mechanism | What it covers |
|-----------|---------------|
| `scripts/docs-example-shadow-patch.sh` | All new/modified Python source files (models, shadow package, CLI, API, scheduler, task-sdk) |
| Inline `cp -r` `RUN` step | Pre-built React UI bundle (Shadow Lane + Shadow Reports tab) |

---

## Prerequisites

| Tool | Minimum version | Purpose |
|------|----------------|---------|
| Docker | 24+ | Build and run images |
| `pnpm` | 9+ | Build the React UI bundle |
| Node.js | 20 LTS | Required by pnpm |

Install pnpm if you don't have it:

```bash
npm install -g pnpm
# or: brew install pnpm
```

---

## Step 1 — Build the React UI bundle

The Shadow Lane grid component and Shadow Reports tab live in the React source tree. They must be compiled to a static bundle **before** running `docker build`.

```bash
cd airflow-core/src/airflow/ui
pnpm install          # install JS dependencies (first time or after pnpm-lock.yaml changes)
pnpm run build        # emits built assets to airflow-core/src/airflow/ui/dist/
```

Expected output: a `dist/` directory containing `index.html`, `assets/`, and bundled JS/CSS.

> **When to re-run:** Any time you change a file under `airflow-core/src/airflow/ui/src/` that belongs to the Shadow DAGs feature:
> - `src/pages/Dag/ShadowReports/ShadowReports.tsx`
> - `src/pages/Dag/ShadowReports/index.ts`
> - `src/layouts/Details/Grid/ShadowLane.tsx`
> - `src/pages/Dag/Dag.tsx` (tab registration)
> - `src/router.tsx` (route registration)
> - `src/layouts/Details/Grid/Bar.tsx` (lane wiring)

---

## Step 2 — Build the Docker image

Run this from the **repository root** (the directory that contains `providers/`, `airflow-core/`, `task-sdk/`):

```bash
docker build \
  -f docs-example/Dockerfile \
  -t atamagrawal/airflow:3.2.0-shadow \
  .
```

**What the build does, in order:**

1. Starts from `apache/airflow:3.2.0` (official image).
2. Copies provider packages (data contracts + UAPE) and Shadow DAG source files into `/tmp/` as root.
3. Fixes ownership of all staged files.
4. Switches to the `airflow` user.
5. `pip install`s the provider packages.
6. Runs `docs-example-shadow-patch.sh` (copied to `/tmp/shadow_patch.sh`), which locates site-packages dynamically and overlays every new/modified `.py` file.
7. Overlays the pre-built `ui/dist/` bundle onto `$AIRFLOW_PKG/ui/dist/`.

To override the Airflow version:

```bash
docker build \
  --build-arg AIRFLOW_IMAGE_TAG=3.2.0-python3.12 \
  --build-arg AIRFLOW_VERSION=3.2.0 \
  -f docs-example/Dockerfile \
  -t atamagrawal/airflow:3.2.0-shadow \
  .
```

---

## Step 3 — Update your docker-compose `.env`

```dotenv
AIRFLOW_IMAGE_NAME=atamagrawal/airflow:3.2.0-shadow
```

Then restart your stack:

```bash
docker compose down
docker compose up -d
```

---

## Step 4 — Run the database migration

The AIP-09 migration (`0110_3_2_0_add_shadow_dag_table`) creates the `shadow_dag` table. Run it **once** after the containers are healthy:

```bash
docker exec -it <your-scheduler-container> airflow db migrate
```

Replace `<your-scheduler-container>` with your actual container name (e.g. `airflow-scheduler-1`). You can find it with:

```bash
docker compose ps
```

Expected log output:

```
INFO  [alembic.runtime.migration] Running upgrade ... -> f4a8c9e2b1d7, Add shadow_dag table
```

The migration is safe to run against an existing database; it chains cleanly from the previous Airflow HEAD revision and is fully reversible with `airflow db downgrade`.

---

## Step 5 — Verify the installation

### CLI

```bash
docker exec -it <scheduler-container> airflow shadow --help
```

Expected:

```
Usage: airflow shadow [OPTIONS] COMMAND [ARGS]...

  Manage Shadow DAGs (AIP-09).

Commands:
  create   Register a shadow DAG pairing.
  discard  Discard a shadow DAG.
  list     List shadow DAGs.
  promote  Promote a shadow DAG to production.
  report   Print the latest comparison report for a shadow DAG.
```

### REST API

```bash
curl http://localhost:8080/api/v2/shadow-dags \
  -H "Authorization: Basic $(echo -n 'admin:admin' | base64)"
```

Expected: `{"shadow_dags": [], "total_entries": 0}`

### Web UI

Open `http://localhost:8080` in a browser. Navigate to any DAG detail page and look for the **Shadow Reports** tab in the top navigation bar.

The **Shadow Lane** appears in the Grid view below each production DAG run row once a shadow run has been created for that production run.

---

## Step 6 — Try it end-to-end

1. **Create a shadow DAG pairing:**

```bash
docker exec -it <scheduler-container> airflow shadow create \
  --production-dag-id my_production_dag \
  --candidate-dag-id my_shadow_dag \
  --ttl-days 14 \
  --divergence-alert-pct 5.0
```

2. **Or use the `@shadow_dag` decorator** (auto-registers on DAG parse):

```python
from airflow.sdk import shadow_dag, dag
from airflow.sdk.bases.operator import BaseOperator

@shadow_dag(production_dag_id="my_production_dag", ttl_days=14)
@dag(schedule="@daily")
def my_shadow_dag():
    ...
```

Place this file in your DAGs folder. The DAG processor picks up the `__shadow__:<config>` tag on next parse cycle and calls `ShadowDagService.create()` automatically.

3. **Inspect reports:**

```bash
docker exec -it <scheduler-container> airflow shadow report \
  --shadow-id <uuid-from-create-output>
```

---

## Patched files reference

The following files are overlaid onto the installed `apache-airflow` package inside the image.

### New files (Python)

| Source path (repo root) | Site-packages overlay path |
|------------------------|---------------------------|
| `airflow-core/src/airflow/shadow/__init__.py` | `airflow/shadow/__init__.py` |
| `airflow-core/src/airflow/shadow/sink_proxy.py` | `airflow/shadow/sink_proxy.py` |
| `airflow-core/src/airflow/shadow/comparison.py` | `airflow/shadow/comparison.py` |
| `airflow-core/src/airflow/shadow/lifecycle.py` | `airflow/shadow/lifecycle.py` |
| `airflow-core/src/airflow/models/shadow_dag.py` | `airflow/models/shadow_dag.py` |
| `airflow-core/src/airflow/migrations/versions/0110_3_2_0_add_shadow_dag_table.py` | `airflow/migrations/versions/0110_3_2_0_add_shadow_dag_table.py` |
| `airflow-core/src/airflow/cli/commands/shadow_command.py` | `airflow/cli/commands/shadow_command.py` |
| `airflow-core/src/airflow/api_fastapi/core_api/datamodels/shadow_dags.py` | `airflow/api_fastapi/core_api/datamodels/shadow_dags.py` |
| `airflow-core/src/airflow/api_fastapi/core_api/routes/public/shadow_dags.py` | `airflow/api_fastapi/core_api/routes/public/shadow_dags.py` |
| `task-sdk/src/airflow/sdk/definitions/shadow.py` | `airflow/sdk/definitions/shadow.py` |

### Modified files (Python)

| Source path (repo root) | What changed |
|------------------------|-------------|
| `airflow-core/src/airflow/models/__init__.py` | `import_all_models()` includes `shadow_dag` |
| `airflow-core/src/airflow/dag_processing/collection.py` | `_auto_register_shadow_dags` hook |
| `airflow-core/src/airflow/jobs/scheduler_job_runner.py` | `_create_shadow_dag_runs` + `_cleanup_expired_shadows` |
| `airflow-core/src/airflow/cli/cli_config.py` | `SHADOW_COMMANDS` group in `core_commands` |
| `airflow-core/src/airflow/api_fastapi/core_api/routes/public/__init__.py` | `shadow_dags_router` included |
| `task-sdk/src/airflow/sdk/__init__.py` | `shadow_dag` lazy export in `__all__` |

### UI bundle

| Source path (repo root) | Site-packages overlay path |
|------------------------|---------------------------|
| `airflow-core/src/airflow/ui/dist/` | `airflow/ui/dist/` |

New UI source files involved (must be present before `pnpm run build`):

- `src/pages/Dag/ShadowReports/ShadowReports.tsx` — Shadow Reports tab page
- `src/pages/Dag/ShadowReports/index.ts` — re-export
- `src/layouts/Details/Grid/ShadowLane.tsx` — shadow run lane in Grid view
- `src/pages/Dag/Dag.tsx` — tab registration
- `src/router.tsx` — route for `shadow_reports`
- `src/layouts/Details/Grid/Bar.tsx` — renders `<ShadowLane>` per production run

---

## Troubleshooting

### `COPY failed: file not found in build context: docs-example/scripts/...` or `scripts/...`

Build **from the repository root** with `.` as the context:

```bash
docker build -f docs-example/Dockerfile -t your-tag .
```

The root `.dockerignore` excludes most directories by default. Only whitelisted
paths (including `scripts/`, `airflow-core/`, `task-sdk/`, `providers/`, `dev/`)
are sent to the Docker daemon.  The AIP-09 patch script therefore lives at
`scripts/docs-example-shadow-patch.sh`, not under `docs-example/scripts/`, so
that `COPY` can see it.  If you still see a missing `scripts/...` file, ensure
the file exists in your tree and you are not using a minimal checkout.

### `COPY failed: file not found in build context: airflow-core/src/airflow/ui/dist`

You haven't built the UI yet. Run Step 1 first:

```bash
cd airflow-core/src/airflow/ui && pnpm install && pnpm run build
```

### `undefined is not an object (evaluating '….task_instances.some')` in the browser

This comes from the **React UI bundle** (TanStack Query `refetchInterval` callbacks), not from
whether a DAG has Shadow DAG metadata. The fix is in `airflow-core/src/airflow/ui` (guards on
`task_instances` and optional `query.state`).

If you still see it:

1. **Rebuild the UI** after pulling changes, then **re-copy** `airflow-core/src/airflow/ui/dist`
   into the image (or rebuild the Docker image that embeds that `dist/`).
2. Rebuild **without** Docker layer cache so the new `dist/` is not skipped:
   `docker build --no-cache -f docs-example/Dockerfile …`
3. **Hard-refresh** the browser (or use a private window) so cached `assets/*.js` chunks are not
   reused. Vite splits code; an old chunk can keep the bug while the rest of the app is new.

### `airflow shadow` command not found

The patch script didn't run or failed silently. Check the build logs for `[shadow-patch]` lines. You can also verify interactively:

```bash
docker exec -it <scheduler-container> \
  python -c "import airflow.shadow; print('shadow package OK')"
```

### `alembic.util.exc.CommandError: Can't locate revision identified by ...`

Your database is on a different Airflow revision chain than expected. Run `airflow db check` to see the current head, then run `airflow db migrate` to bring it up to date including the shadow table.

### Shadow tab not showing in web UI

The UI bundle in the image must include the Shadow DAG components. Confirm the dist was built after the source changes by checking the build timestamp:

```bash
docker exec -it <webserver-container> \
  ls -la $(python -c "import airflow, os; print(os.path.dirname(airflow.__file__))")/ui/dist/
```

If the timestamp predates your last `pnpm run build`, rebuild the image.

### Shadow runs not being created

The scheduler integration requires the `shadow_dag` table to exist (Step 4). Also verify the shadow DAG is in `ACTIVE` status:

```bash
docker exec -it <scheduler-container> airflow shadow list
```

---

## Pushing to a registry

```bash
docker push atamagrawal/airflow:3.2.0-shadow
```

Other team members can then pull and use the image directly without needing Node.js or the repo locally:

```dotenv
AIRFLOW_IMAGE_NAME=atamagrawal/airflow:3.2.0-shadow
```
