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

/**
 * Shadow Lane — AIP-09.
 *
 * Displayed below each production DAG run column in the Grid view.
 * Shows the state of the corresponding shadow run (if one exists) using the
 * same badge pattern as GridTI, but with an amber border to distinguish it.
 */

import { Badge, Box, Flex, Tooltip } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ShadowRunInfo {
  run_id: string;
  shadow_id: string;
  state: string | null;
  verdict: string | null;
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

const fetchShadowRunsForDag = async (dagId: string): Promise<ShadowRunInfo[]> => {
  // Fetch active shadow dags for the production DAG
  const res = await fetch(
    `/api/v2/shadow-dags?production_dag_id=${encodeURIComponent(dagId)}&status_filter=active`,
  );
  if (!res.ok) return [];
  const data = (await res.json()) as { shadow_dags: Array<{ shadow_id: string }> };

  // For each shadow, fetch the latest comparison report to get verdict
  const infos: ShadowRunInfo[] = [];
  for (const s of data.shadow_dags) {
    const rRes = await fetch(
      `/api/v2/shadow-dags/${encodeURIComponent(s.shadow_id)}/reports/latest`,
    );
    if (rRes.status === 404) {
      infos.push({ run_id: "", shadow_id: s.shadow_id, state: "queued", verdict: null });
    } else if (rRes.ok) {
      const r = (await rRes.json()) as {
        run_id: string;
        verdict: string;
      };
      infos.push({
        run_id: r.run_id,
        shadow_id: s.shadow_id,
        state: r.verdict === "SHADOW_FAILED" ? "failed" : "success",
        verdict: r.verdict,
      });
    }
  }
  return infos;
};

// ---------------------------------------------------------------------------
// Verdict → state color mapping
// ---------------------------------------------------------------------------

const VERDICT_PALETTE: Record<string, string> = {
  DIVERGED: "red",
  MATCH: "success",
  SHADOW_FAILED: "failed",
  WITHIN_THRESHOLD: "yellow",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ShadowLaneProps {
  /** run_id of the production DagRun this column represents */
  readonly productionRunId: string;
}

export const ShadowLane = ({ productionRunId }: ShadowLaneProps) => {
  const { dagId = "" } = useParams();

  const { data: shadowRuns } = useQuery({
    queryFn: () => fetchShadowRunsForDag(dagId),
    queryKey: ["shadow-lane", dagId],
    // Shadow lane data is relatively stable — refresh every 60 s
    staleTime: 60_000,
  });

  if (!shadowRuns?.length) return null;

  // Find the shadow run that corresponds to this production run
  const matching = shadowRuns.filter(
    (s) => !s.run_id || s.run_id.includes(productionRunId),
  );

  if (!matching.length) return null;

  return (
    <Flex
      borderColor="orange.200"
      borderTop="2px solid"
      flexDirection="column"
      gap={0.5}
      mt={1}
      pt={0.5}
    >
      {matching.map((run) => (
        <Tooltip
          content={`Shadow: ${run.shadow_id} — ${run.verdict ?? "pending"}`}
          key={run.shadow_id}
        >
          <Badge
            alignItems="center"
            borderColor="orange.300"
            borderRadius={4}
            borderWidth="1px"
            colorPalette={VERDICT_PALETTE[run.verdict ?? ""] ?? "stone"}
            data-testid="shadow-state-badge"
            display="flex"
            height="10px"
            justifyContent="center"
            minH={0}
            p={0}
            variant="solid"
            width="14px"
          />
        </Tooltip>
      ))}
    </Flex>
  );
};
