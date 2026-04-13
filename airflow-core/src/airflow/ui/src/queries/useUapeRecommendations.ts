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
import { useQuery } from "@tanstack/react-query";
import axios from "axios";

import { OpenAPI } from "openapi/requests/core/OpenAPI";

export type TaskClassification = {
  opacity: "clear" | "opaque";
  opacity_reason: string;
  task_id: string;
  task_type: string | null;
};

export type OverlapProof = {
  detail: string;
  kind: string;
};

export type OverlapHint = {
  caveat: string;
  proof: OverlapProof;
  recommendation: string;
  task_a: string;
  task_b: string;
};

export type AbstainedHint = {
  abstain_reason: string;
  proof: OverlapProof;
  task_a: string;
  task_b: string;
};

export type UapeReport = {
  abstained_parallel_hints: Array<AbstainedHint>;
  clear_operator_allowlist: Array<string>;
  clear_task_overlap_hints: Array<OverlapHint>;
  dag_id: string;
  policy: string;
  structurally_independent_pairs: Array<{ proof: OverlapProof; task_a: string; task_b: string }>;
  task_classifications: Array<TaskClassification>;
};

const fetchUapeRecommendations = async (dagId: string): Promise<UapeReport> => {
  const { data } = await axios.get<UapeReport>(
    `${OpenAPI.BASE}/uape/dags/${encodeURIComponent(dagId)}/recommendations.json`,
  );

  return data;
};

export const useUapeRecommendationsKey = "uapeRecommendations";

export const useUapeRecommendations = (dagId: string) =>
  useQuery<UapeReport>({
    enabled: Boolean(dagId),
    queryFn: () => fetchUapeRecommendations(dagId),
    queryKey: [useUapeRecommendationsKey, dagId],
    retry: false,
  });
