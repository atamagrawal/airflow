#!/usr/bin/env bash
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
#
# AIP-09 Shadow DAGs — site-packages patch (used by docs-example/Dockerfile).
#
# The repository root .dockerignore excludes most paths; `scripts/` is
# whitelisted, so this file must live under scripts/ to be available in
# the Docker build context.  See .dockerignore at the repository root.
#
# Layout expected in /tmp/shadow-patch:
#   airflow/   — files to overlay onto the airflow-core site-packages tree
#   sdk/       — files to overlay onto the task-sdk site-packages tree (airflow/sdk/)

set -euo pipefail

# ── Locate site-packages ──────────────────────────────────────────────────────
AIRFLOW_PKG=$(python -c "import airflow, os; print(os.path.dirname(airflow.__file__))")
SDK_PKG=$(python -c "import airflow.sdk, os; print(os.path.dirname(airflow.sdk.__file__))")

echo "[shadow-patch] airflow-core site-packages : $AIRFLOW_PKG"
echo "[shadow-patch] task-sdk site-packages     : $SDK_PKG"

# ── Overlay airflow-core files ────────────────────────────────────────────────
cp -v /tmp/shadow-patch/airflow/models/shadow_dag.py \
      "$AIRFLOW_PKG/models/shadow_dag.py"

cp -v /tmp/shadow-patch/airflow/models/__init__.py \
      "$AIRFLOW_PKG/models/__init__.py"

cp -v /tmp/shadow-patch/airflow/models/dagrun.py \
      "$AIRFLOW_PKG/models/dagrun.py"

mkdir -p "$AIRFLOW_PKG/shadow"
cp -rv /tmp/shadow-patch/airflow/shadow/. \
       "$AIRFLOW_PKG/shadow/"

cp -v /tmp/shadow-patch/airflow/migrations/versions/0110_3_2_0_add_shadow_dag_table.py \
      "$AIRFLOW_PKG/migrations/versions/0110_3_2_0_add_shadow_dag_table.py"

cp -v /tmp/shadow-patch/airflow/cli/commands/shadow_command.py \
      "$AIRFLOW_PKG/cli/commands/shadow_command.py"

cp -v /tmp/shadow-patch/airflow/cli/cli_config.py \
      "$AIRFLOW_PKG/cli/cli_config.py"

cp -v /tmp/shadow-patch/airflow/api_fastapi/core_api/datamodels/shadow_dags.py \
      "$AIRFLOW_PKG/api_fastapi/core_api/datamodels/shadow_dags.py"

cp -v /tmp/shadow-patch/airflow/api_fastapi/core_api/routes/public/shadow_dags.py \
      "$AIRFLOW_PKG/api_fastapi/core_api/routes/public/shadow_dags.py"

cp -v /tmp/shadow-patch/airflow/api_fastapi/core_api/routes/public/__init__.py \
      "$AIRFLOW_PKG/api_fastapi/core_api/routes/public/__init__.py"

cp -v /tmp/shadow-patch/airflow/dag_processing/collection.py \
      "$AIRFLOW_PKG/dag_processing/collection.py"

cp -v /tmp/shadow-patch/airflow/jobs/scheduler_job_runner.py \
      "$AIRFLOW_PKG/jobs/scheduler_job_runner.py"

cp -v /tmp/shadow-patch/airflow/api/common/trigger_dag.py \
      "$AIRFLOW_PKG/api/common/trigger_dag.py"

cp -v /tmp/shadow-patch/airflow/api_fastapi/core_api/routes/public/dag_run.py \
      "$AIRFLOW_PKG/api_fastapi/core_api/routes/public/dag_run.py"

# ── Overlay task-sdk files ────────────────────────────────────────────────────
cp -v /tmp/shadow-patch/sdk/__init__.py \
      "$SDK_PKG/__init__.py"

mkdir -p "$SDK_PKG/definitions"
cp -v /tmp/shadow-patch/sdk/definitions/shadow.py \
      "$SDK_PKG/definitions/shadow.py"

echo "[shadow-patch] Done."
