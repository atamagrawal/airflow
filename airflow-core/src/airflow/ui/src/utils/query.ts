/*!
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */
import { useDagRunServiceGetDagRuns, useDagServiceGetDagDetails } from "openapi/queries";
import type { TaskInstanceState } from "openapi/requests/types.gen";
import { useConfig } from "src/queries/useConfig";

export const isStatePending = (state?: TaskInstanceState | null) =>
  state === "deferred" ||
  state === "scheduled" ||
  state === "running" ||
  state === "up_for_reschedule" ||
  state === "up_for_retry" ||
  state === "queued" ||
  state === "restarting" ||
  !Boolean(state);

/**
 * TanStack Query refetch callbacks often read `query.state.data` from OpenAPI
 * responses.  Those shapes can be partial when the request errors or the
 * backend returns an unexpected body — `data` may exist while nested arrays
 * (e.g. `task_instances`) are missing.  Optional chaining on `data` alone is
 * not enough: use these guards before calling `.some` on nested arrays.
 */
export const hasPendingTaskInstanceRows = (
  data: { task_instances?: Array<{ state?: TaskInstanceState | null }> } | undefined,
): boolean => {
  // Support partial API responses: `data` may exist as `{}` with `task_instances` omitted.
  // Also tolerate undefined `data` when callers pass `query.state.data` and state is empty.
  const rows = data?.task_instances;
  return Array.isArray(rows) && rows.some((ti) => isStatePending(ti.state));
};

/** Safe for `refetchInterval` when the query object may be incomplete (edge runtimes). */
export const hasPendingTaskInstanceRowsFromQuery = (query: {
  state?: { data?: { task_instances?: Array<{ state?: TaskInstanceState | null }> } };
}): boolean => hasPendingTaskInstanceRows(query.state?.data);

export const hasPendingDagRunRows = (
  data: { dag_runs?: Array<{ state?: TaskInstanceState | null }> } | undefined,
): boolean => {
  const rows = data?.dag_runs;
  return Array.isArray(rows) && rows.some((r) => isStatePending(r.state));
};

type DagWithLatestRuns = {
  is_paused?: boolean;
  latest_dag_runs?: Array<{ state?: TaskInstanceState | null }>;
};

export const hasUnpausedDagWithPendingLatestRun = (
  data: { dags?: DagWithLatestRuns[] } | undefined,
): boolean => {
  const dags = data?.dags;
  if (!Array.isArray(dags)) {
    return false;
  }
  return dags.some(
    (dag) =>
      !dag.is_paused &&
      Array.isArray(dag.latest_dag_runs) &&
      dag.latest_dag_runs.some((dr) => isStatePending(dr.state)),
  );
};

type BackfillListRow = { completed_at: string | null; is_paused: boolean };

export const hasActiveUnfinishedBackfill = (
  data: { backfills?: BackfillListRow[] } | undefined,
): boolean => {
  const rows = data?.backfills;
  return (
    Array.isArray(rows) && rows.some((bf) => bf.completed_at === null && !bf.is_paused)
  );
};

type HitlDetailRow = {
  responded_at: string | undefined;
  task_instance?: { state?: string };
};

export const hasDeferredHitlWithoutResponse = (
  data: { hitl_details?: HitlDetailRow[] } | undefined,
): boolean => {
  const rows = data?.hitl_details;
  return (
    Array.isArray(rows) &&
    rows.some(
      (d) => d.responded_at === undefined && d.task_instance?.state === "deferred",
    )
  );
};

type GridRunRow = { state?: TaskInstanceState | null };

export const hasPendingGridRunRows = (data: unknown): boolean =>
  Array.isArray(data) &&
  (data as GridRunRow[]).some((run) => isStatePending(run.state));

// checkPendingRuns=false assumes that the component is already handling pending, setting to true will have useAutoRefresh handle it
export const useAutoRefresh = ({
  checkPendingRuns = false,
  dagId,
}: {
  checkPendingRuns?: boolean;
  dagId?: string;
}) => {
  const autoRefreshInterval = useConfig("auto_refresh_interval") as number | undefined;
  const { data: dag } = useDagServiceGetDagDetails(
    {
      dagId: dagId ?? "",
    },
    undefined,
    { enabled: dagId !== undefined },
  );

  const { data: dagRunData } = useDagRunServiceGetDagRuns(
    {
      dagId: dagId ?? "~",
      limit: 1,
      state: ["running", "queued"],
    },
    undefined,
    // Scale back refetching to 10x longer if there are no pending runs (eg: every 3 secs for active runs, otherwise 30 secs)
    {
      enabled: checkPendingRuns,
      refetchInterval: (query) =>
        autoRefreshInterval !== undefined &&
        ((query.state.data?.dag_runs ?? []).length > 0
          ? autoRefreshInterval * 1000
          : autoRefreshInterval * 10 * 1000),
    },
  );

  const pendingRuns = checkPendingRuns ? (dagRunData?.dag_runs ?? []).length >= 1 : true;

  const paused = Boolean(dagId) ? dag?.is_paused : false;

  const canRefresh = autoRefreshInterval !== undefined && !paused && pendingRuns;

  // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
  return (canRefresh ? autoRefreshInterval * 1000 : false) as number | false;
};
