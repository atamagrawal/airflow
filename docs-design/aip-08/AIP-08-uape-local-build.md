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

# UAPE v2 — Local Build & Setup Guide

**Provider:** `apache-airflow-providers-uape` v0.2.0  
**Code location:** `dev/uape-provider/`  
**Implementation notes:** `AIP-08-uape-v2-implementation-notes.md`

---

## Prerequisites

- Airflow 3.0+ dev environment set up (see
  [`contributing-docs/03a_contributors_quick_start_beginners.rst`](../../contributing-docs/03a_contributors_quick_start_beginners.rst))
- `uv` installed (`pip install uv` or `brew install uv`)
- Docker Desktop (Option B only)

---

## Option A — Install into the dev venv (fastest iteration)

Run all commands from the **repo root**.

### uv command reference

| Goal | Command |
|------|---------|
| **Build a wheel + sdist (compile/package)** | **`uv build --project dev/uape-provider`** |
| Install from the built wheel | `uv pip install dev/uape-provider/dist/apache_airflow_providers_uape-0.2.0-py3-none-any.whl` |
| Install editable (development, no rebuild needed) | `uv pip install -e dev/uape-provider` |
| Install with dev extras (adds pytest) | `uv pip install -e "dev/uape-provider[dev]"` |
| Sync declared deps only | `uv sync --project dev/uape-provider` |
| Pre-compile `.py` → `.pyc` bytecode (optional) | `python3 -m compileall dev/uape-provider/src/` |

### 1. Install the provider editable

```bash
uv pip install -e dev/uape-provider
```

This installs `apache-airflow-providers-uape` and its dependencies (`networkx`, `numpy`,
`scipy`) into the same venv as Airflow. Editable mode means code changes in
`dev/uape-provider/src/` take effect immediately — no reinstall needed.

### Build (compile/package) a wheel

```bash
uv build --project dev/uape-provider
```

Outputs in `dev/uape-provider/dist/`:

```
apache_airflow_providers_uape-0.2.0-py3-none-any.whl   ← installable wheel
apache_airflow_providers_uape-0.2.0.tar.gz             ← source archive
```

Install the wheel anywhere without needing the source tree:

```bash
uv pip install dev/uape-provider/dist/apache_airflow_providers_uape-0.2.0-py3-none-any.whl
```

Use the wheel in a Dockerfile instead of copying the whole source:

```dockerfile
COPY dev/uape-provider/dist/apache_airflow_providers_uape-0.2.0-py3-none-any.whl /tmp/
RUN pip install /tmp/apache_airflow_providers_uape-0.2.0-py3-none-any.whl
```

### Install editable (for active development)

```bash
uv pip install -e dev/uape-provider
```

Editable mode means code changes in `dev/uape-provider/src/` take effect immediately —
no rebuild needed.

### 2. Start Airflow

```bash
breeze start-airflow
```

The API server discovers the plugin via the `airflow.plugins` entry point registered in
`dev/uape-provider/pyproject.toml`. The `/uape` HTTP mount and the **Recommendations** UI
tab are active once the server is up.

### 3. Open the UI

Navigate to any parsed DAG in the Airflow UI and click the **Recommendations** tab:

```
http://localhost:28080/uape/dags/<dag_id>/recommendations-ui
```

Or call the JSON API directly:

```bash
curl -u admin:admin \
  http://localhost:28080/uape/dags/<dag_id>/recommendations.json \
  | python3 -m json.tool
```

### 4. Use the CLI

```bash
# Human-readable analysis (all edges)
airflow uape analyze <dag_id>

# Only edges recommended for removal
airflow uape analyze <dag_id> --verdict remove

# Skip Monte Carlo simulation (faster, no historical data needed)
airflow uape analyze <dag_id> --no-simulate

# Full JSON report to file
airflow uape export <dag_id> > report.json

# JSON, uncertain edges only
airflow uape analyze <dag_id> --format json --verdict uncertain
```

---

## Option B — Docker (isolated, production-like)

The example Dockerfile at `example/Dockerfile` extends `apache/airflow:3.2.0` and installs
the UAPE provider alongside the data-contracts providers.

### 1. Build from the repo root

The `COPY` directives in the Dockerfile use paths relative to the repo root, so the build
context **must** be the repo root:

```bash
docker build -f example/Dockerfile -t my-airflow:uape-v2 .
```

Override the Airflow version at build time if needed:

```bash
docker build -f example/Dockerfile \
  --build-arg AIRFLOW_VERSION=3.2.0 \
  --build-arg AIRFLOW_IMAGE_TAG=3.2.0-python3.12 \
  -t my-airflow:uape-v2 .
```

### 2. Point docker-compose at the new image

In the `.env` file next to your `docker-compose.yaml`:

```bash
AIRFLOW_IMAGE_NAME=my-airflow:uape-v2
```

Then:

```bash
docker compose up
```

### 3. (Optional) Push to Docker Hub

```bash
# Replace with your own registry/repo
docker tag my-airflow:uape-v2 <your-dockerhub-user>/airflow:uape-v2
docker push <your-dockerhub-user>/airflow:uape-v2
```

---

## Option C — Run unit tests only (no Airflow server needed)

```bash
uv run pytest dev/uape-provider/tests/test_parallelization.py -xvs
```

44 tests cover all four signals in isolation, the scoring engine, the duration profiler,
the Monte Carlo simulator, and the end-to-end `analyze_dag_edges` pipeline.

Run a single test class or method:

```bash
uv run pytest dev/uape-provider/tests/test_parallelization.py \
  ::TestSignalAssetOverlap -xvs

uv run pytest dev/uape-provider/tests/test_parallelization.py \
  ::TestAnalyzeDagEdges::test_full_pipeline_schema -xvs
```

---

## Sanity checks after install

```bash
# Confirm the Airflow plugin is registered
airflow plugins list | grep uape

# Confirm the CLI command group is registered
airflow uape --help
```

Expected output from `airflow plugins list`:

```
uape_plugin | ... | uape |  ...
```

Expected output from `airflow uape --help`:

```
Usage: airflow uape [OPTIONS] COMMAND [ARGS]...

  UAPE: Uncertainty-Aware Parallelization Engine.

Commands:
  analyze  Analyse declared DAG edges for false dependencies.
  export   Export the full UAPE JSON report for a DAG.
```

---

## What happens on first use

The first request to `/uape/dags/<dag_id>/recommendations.json` (or the equivalent CLI
command) triggers:

1. **Table creation** — `uape_report_cache` is created in the metadata DB
   (`CREATE TABLE IF NOT EXISTS`). No Alembic migration required.
2. **Full analysis** — all declared edges are scored (4 signals), and Monte Carlo simulation
   runs for any `remove` verdict with sufficient historical data.
3. **Cache write** — the JSON report is stored in `uape_report_cache`.

Subsequent requests within the same DAG version and run history return the cached result
instantly (two cheap scalar queries + a hash compare). The cache is invalidated automatically
when:

| Trigger | How detected |
|---------|-------------|
| DAG file edited and re-parsed | `SerializedDagModel.last_updated` changes |
| New DAG run triggered | `MAX(DagRun.start_date)` changes |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `airflow uape` not found | Provider not installed or plugin not loaded | Run `uv pip install -e dev/uape-provider` and restart the API server |
| Recommendations tab missing | Plugin registered but UI build does not support `category` grouping | Build the UI from source (`pnpm run build` in `airflow-core/src/airflow/ui`) or use Breeze |
| `No serialized DAG found` | DAG has not been parsed yet | Trigger a DAG parse: `airflow dags reserialize` or wait for the Dag Processor cycle |
| Monte Carlo results absent | Fewer than 5 historical successful runs per task | Run the DAG a few times, or use `--no-simulate` to skip simulation |
| `uape_report_cache` table missing on startup | API server did not reach the `startup` event | Check API server logs for import errors in the UAPE plugin |
