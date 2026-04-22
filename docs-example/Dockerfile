# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

# This is an example Dockerfile for local testing. It is not intended for production use.
#
# Extends the official Airflow image with:
#
#   1. In-repo **data contracts** provider + decorators package (not on PyPI).
#   2. In-repo **UAPE** dev provider v2 (``dev/uape-provider``): CLI ``airflow uape``,
#      ``/uape`` HTTP routes, and a Recommendations-category view on DAG/Run/Task pages.
#   3. **AIP-09 Shadow DAGs** core patch: new ``airflow.shadow`` package, ``ShadowDag``
#      ORM model, Alembic migration, ``@shadow_dag`` decorator, ``airflow shadow`` CLI,
#      REST API at ``/api/v2/shadow-dags``, scheduler integration, and React UI additions.
#
# ── Shadow DAG patch notes ───────────────────────────────────────────────────────────────
#
# Because Shadow DAGs are implemented as changes to airflow-core and task-sdk (not a
# provider), they cannot be pip-installed. Instead, the patch copies new and modified
# Python source files directly over the installed package in site-packages at build time.
# A helper script (``scripts/docs-example-shadow-patch.sh``) performs the copy using
# ``python -c "import airflow; ..."`` to locate site-packages dynamically.  The script
# lives under the repo's ``scripts/`` directory because the root ``.dockerignore`` whitelists
# ``scripts/`` but not ``docs-example/`` — otherwise ``COPY`` would fail to find the file.
#
# After building, run the DB migration inside the container:
#
#   docker exec -it <scheduler-container> airflow db migrate
#
# The migration (revision f4a8c9e2b1d7) creates the ``shadow_dag`` table. It is safe to
# run against an existing database; it chains from the previous HEAD migration.
#
# ── UI patch notes ───────────────────────────────────────────────────────────────────────
#
# The Shadow Reports tab and Shadow Lane Grid component require a pre-built UI bundle.
# Run the following **before** ``docker build`` (once, or whenever UI source changes):
#
#   cd airflow-core/src/airflow/ui
#   pnpm install
#   pnpm run build          # writes to airflow-core/src/airflow/ui/dist/
#
# This Dockerfile then COPYs that dist/ folder and overlays it onto the image's
# installed ``airflow/ui/dist/``.  If the dist/ folder is missing at build time the
# COPY instruction will fail — build the UI first.
#
# ── Tab grouping / category UI ───────────────────────────────────────────────────────────
#
# Tab grouping by plugin ``category`` (one parent Recommendations tab, per-view sub-nav)
# also requires this same built UI, so building once covers both features.
#
# ── Build & run ──────────────────────────────────────────────────────────────────────────
#
# Build from the **repository root** (the directory that contains ``providers/``):
#
#   docker build -f docs-example/Dockerfile -t atamagrawal/airflow:3.2.0-shadow .
#
# Push to Docker Hub:
#
#   docker push atamagrawal/airflow:3.2.0-shadow
#
# Point your docker-compose quick-start at this image in ``.env``:
#
#   AIRFLOW_IMAGE_NAME=atamagrawal/airflow:3.2.0-shadow
#
# ``AIRFLOW_IMAGE_TAG`` — ``apache/airflow`` image tag (e.g. ``3.2.0`` or ``3.2.0-python3.12``).
# ``AIRFLOW_VERSION``   — semver for ``pip install apache-airflow==...`` (X.Y.Z only).
# Override at build time:
#   docker build --build-arg AIRFLOW_VERSION=3.2.0 --build-arg AIRFLOW_IMAGE_TAG=3.2.0-python3.12 ...

ARG AIRFLOW_IMAGE_TAG=3.2.0
ARG AIRFLOW_VERSION=3.2.0

FROM apache/airflow:${AIRFLOW_IMAGE_TAG}

ARG AIRFLOW_VERSION
ENV AIRFLOW_VERSION=${AIRFLOW_VERSION}

USER root

# ── Provider packages (data contracts + UAPE) ────────────────────────────────────────────
COPY providers/data/contracts /tmp/apache-airflow-providers-data-contracts
COPY providers/data/contracts_decorators /tmp/apache-airflow-providers-data-contracts-decorators
COPY dev/uape-provider /tmp/apache-airflow-providers-uape

# ── AIP-09 Shadow DAGs: new files ────────────────────────────────────────────────────────
# New package: airflow.shadow (sink_proxy, comparison, lifecycle)
COPY airflow-core/src/airflow/shadow /tmp/shadow-patch/airflow/shadow

# New ORM model
COPY airflow-core/src/airflow/models/shadow_dag.py /tmp/shadow-patch/airflow/models/shadow_dag.py

# New Alembic migration
COPY airflow-core/src/airflow/migrations/versions/0110_3_2_0_add_shadow_dag_table.py \
     /tmp/shadow-patch/airflow/migrations/versions/0110_3_2_0_add_shadow_dag_table.py

# New CLI command module
COPY airflow-core/src/airflow/cli/commands/shadow_command.py \
     /tmp/shadow-patch/airflow/cli/commands/shadow_command.py

# New REST API datamodels + router
COPY airflow-core/src/airflow/api_fastapi/core_api/datamodels/shadow_dags.py \
     /tmp/shadow-patch/airflow/api_fastapi/core_api/datamodels/shadow_dags.py
COPY airflow-core/src/airflow/api_fastapi/core_api/routes/public/shadow_dags.py \
     /tmp/shadow-patch/airflow/api_fastapi/core_api/routes/public/shadow_dags.py

# New @shadow_dag decorator (task-sdk)
COPY task-sdk/src/airflow/sdk/definitions/shadow.py \
     /tmp/shadow-patch/sdk/definitions/shadow.py

# ── AIP-09 Shadow DAGs: modified files ───────────────────────────────────────────────────
# models/__init__.py — import_all_models() includes shadow_dag
COPY airflow-core/src/airflow/models/__init__.py \
     /tmp/shadow-patch/airflow/models/__init__.py

# dag_processing/collection.py — _auto_register_shadow_dags hook
COPY airflow-core/src/airflow/dag_processing/collection.py \
     /tmp/shadow-patch/airflow/dag_processing/collection.py

# jobs/scheduler_job_runner.py — _create_shadow_dag_runs + _cleanup_expired_shadows
COPY airflow-core/src/airflow/jobs/scheduler_job_runner.py \
     /tmp/shadow-patch/airflow/jobs/scheduler_job_runner.py

# cli/cli_config.py — SHADOW_COMMANDS group registered in core_commands
COPY airflow-core/src/airflow/cli/cli_config.py \
     /tmp/shadow-patch/airflow/cli/cli_config.py

# routes/public/__init__.py — authenticated_router.include_router(shadow_dags_router)
COPY airflow-core/src/airflow/api_fastapi/core_api/routes/public/__init__.py \
     /tmp/shadow-patch/airflow/api_fastapi/core_api/routes/public/__init__.py

# task-sdk sdk/__init__.py — "shadow_dag" lazy export
COPY task-sdk/src/airflow/sdk/__init__.py \
     /tmp/shadow-patch/sdk/__init__.py

# ── AIP-09 Shadow DAGs: pre-built React UI bundle ────────────────────────────────────────
# Build the UI before running docker build:
#   cd airflow-core/src/airflow/ui && pnpm install && pnpm run build
COPY airflow-core/src/airflow/ui/dist /tmp/shadow-ui-dist

# ── Patch helper script (must be under whitelisted `scripts/` — see .dockerignore) ───────────
COPY scripts/docs-example-shadow-patch.sh /tmp/shadow_patch.sh

# COPY is root-owned; fix ownership before switching to airflow user.
RUN chown -R airflow:root \
        /tmp/apache-airflow-providers-data-contracts \
        /tmp/apache-airflow-providers-data-contracts-decorators \
        /tmp/apache-airflow-providers-uape \
        /tmp/shadow-patch \
        /tmp/shadow-ui-dist \
        /tmp/shadow_patch.sh \
    && chmod +x /tmp/shadow_patch.sh

USER airflow

# ── Install providers (pip) ───────────────────────────────────────────────────────────────
# Data-contracts decorators import ``determine_kwargs`` from common-compat's sdk shim (added
# in 1.14.1). The base image may still ship 1.14.0 which breaks DAG parse — pin high enough.
RUN pip install --no-cache-dir \
    "apache-airflow==${AIRFLOW_VERSION}" \
    "apache-airflow-providers-common-compat>=1.14.1" \
    /tmp/apache-airflow-providers-data-contracts \
    /tmp/apache-airflow-providers-data-contracts-decorators \
    /tmp/apache-airflow-providers-uape \
    && rm -rf /tmp/apache-airflow-providers-data-contracts \
        /tmp/apache-airflow-providers-data-contracts-decorators \
        /tmp/apache-airflow-providers-uape

# ── Apply AIP-09 Shadow DAG source patch ─────────────────────────────────────────────────
# Copies new/modified .py files into the installed airflow + task-sdk site-packages trees.
# Uses python to locate site-packages dynamically (works with any Python micro-version).
RUN /tmp/shadow_patch.sh && rm -rf /tmp/shadow-patch /tmp/shadow_patch.sh

# ── Apply AIP-09 UI bundle ────────────────────────────────────────────────────────────────
# Overlays the pre-built React dist onto the installed airflow/ui/dist/ in site-packages,
# enabling the Shadow Lane grid component and Shadow Reports tab in the web UI.
RUN AIRFLOW_PKG=$(python -c "import airflow, os; print(os.path.dirname(airflow.__file__))") \
    && cp -r /tmp/shadow-ui-dist/. "$AIRFLOW_PKG/ui/dist/" \
    && rm -rf /tmp/shadow-ui-dist
