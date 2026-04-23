#
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
"""Create shadow :class:`~airflow.models.dagrun.DagRun` rows alongside production runs (AIP-09)."""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from airflow.models.dagrun import DagRun, DagRunState
from airflow.models.serialized_dag import SerializedDagModel
from airflow.models.shadow_dag import ShadowDag, ShadowDagStatus
from airflow.utils.types import DagRunTriggeredByType, DagRunType

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def _data_interval_tuple_from_dag_run(dr: DagRun) -> tuple[datetime, datetime] | None:
    s, e = dr.data_interval_start, dr.data_interval_end
    if s is not None and e is not None:
        return (s, e)
    return None


def get_serialized_dag_for_shadow_candidate(dag_id: str, session: Session):
    """
    Return the *candidate* production DAG (serialized) for creating shadow task runs, or None.

    Mirrors the scheduler's deserialization path: links are not needed for run creation.
    """
    try:
        serdag = SerializedDagModel.get(dag_id=dag_id, session=session)
        if not serdag:
            return None
        serdag.load_op_links = False
        return serdag.dag
    except Exception:
        log.exception("Failed to deserialize DAG '%s' for shadow run creation", dag_id)
        return None


def create_shadow_dag_runs_for_production_dag_runs(
    production_dag_runs: Collection[DagRun],
    *,
    session: Session,
    creating_job_id: int | None = None,
) -> None:
    """
    For each *production* DagRun, create a parallel shadow DagRun for each eligible experiment.

    Safe to call after any production :meth:`~airflow.serialization.definitions.dag.SerializedDAG.create_dagrun`
    (scheduler, API trigger, etc.).  Failures are logged; **this function never raises**.

    :param production_dag_runs: Newly created production runs (same logical date / interval as the shadow run).
    :param session: ORM session that already contains the production runs.
    :param creating_job_id: ``creating_job_id`` for the shadow run (scheduler job id), or ``None`` for API/CLI.
    """
    if not production_dag_runs:
        return
    try:
        prod_dag_ids = {dr.dag_id for dr in production_dag_runs}
        eligible_shadows: list[ShadowDag] = list(
            session.scalars(
                select(ShadowDag).where(
                    ShadowDag.production_dag_id.in_(prod_dag_ids),
                    ShadowDag.status.in_(
                        (
                            ShadowDagStatus.REGISTERED.value,
                            ShadowDagStatus.ACTIVE.value,
                        )
                    ),
                )
            )
        )
        if not eligible_shadows:
            return

        shadow_map: dict[str, list[ShadowDag]] = {}
        for s in eligible_shadows:
            shadow_map.setdefault(s.production_dag_id, []).append(s)

        for prod_run in production_dag_runs:
            triggered_by = prod_run.triggered_by or DagRunTriggeredByType.REST_API
            for shadow in shadow_map.get(prod_run.dag_id, []):
                candidate_dag_id = shadow.candidate_dag_id
                serdag = get_serialized_dag_for_shadow_candidate(candidate_dag_id, session=session)
                if not serdag:
                    log.warning(
                        "Shadow candidate DAG '%s' not found in serialized_dag; skipping shadow run.",
                        candidate_dag_id,
                    )
                    continue
                shadow_run_id = f"shadow__{shadow.shadow_id}__{prod_run.run_id}"
                try:
                    serdag.create_dagrun(
                        run_id=shadow_run_id,
                        logical_date=prod_run.logical_date,
                        data_interval=_data_interval_tuple_from_dag_run(prod_run),
                        run_after=prod_run.run_after,
                        run_type=DagRunType.MANUAL,
                        triggered_by=triggered_by,
                        state=DagRunState.QUEUED,
                        creating_job_id=creating_job_id,
                        conf={
                            "__shadow_run__": True,
                            "__shadow_id__": shadow.shadow_id,
                            "__prod_run_id__": prod_run.run_id,
                            "__prod_dag_id__": prod_run.dag_id,
                        },
                        session=session,
                    )
                    log.info(
                        "Spawned shadow DagRun '%s' for production run '%s' (shadow=%s).",
                        shadow_run_id,
                        prod_run.run_id,
                        shadow.shadow_id,
                    )
                except Exception:
                    log.exception(
                        "Failed to create shadow DagRun for production run '%s' (shadow=%s).",
                        prod_run.run_id,
                        shadow.shadow_id,
                    )
    except Exception:
        log.exception("Unexpected error in create_shadow_dag_runs_for_production_dag_runs — skipped.")
