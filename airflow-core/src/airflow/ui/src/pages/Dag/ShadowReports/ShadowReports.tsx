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
 * Shadow Reports tab — AIP-09.
 *
 * Displays all Shadow DAG experiments registered for the current DAG plus
 * the latest comparison report for each one.
 */
import { Badge, Box, Flex, Heading, Spinner, Table, Text } from "@chakra-ui/react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ShadowDagRecord {
  candidate_dag_id: string;
  created_at: string;
  divergence_alert_pct: number;
  expires_at: string;
  notify: string | null;
  production_dag_id: string;
  shadow_id: string;
  status: string;
  ttl_days: number;
}

interface ShadowDagCollection {
  shadow_dags: ShadowDagRecord[];
  total_entries: number;
}

interface ComparisonReport {
  error: string | null;
  row_count_delta_pct: number;
  row_count_prod: number;
  row_count_shadow: number;
  run_id: string;
  shadow_id: string;
  verdict: string;
}

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

const fetchShadowDags = async (dagId: string): Promise<ShadowDagCollection> => {
  const res = await fetch(`/api/v2/shadow-dags?production_dag_id=${encodeURIComponent(dagId)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<ShadowDagCollection>;
};

const fetchLatestReport = async (shadowId: string): Promise<ComparisonReport | null> => {
  const res = await fetch(`/api/v2/shadow-dags/${encodeURIComponent(shadowId)}/reports/latest`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<ComparisonReport>;
};

// ---------------------------------------------------------------------------
// Verdict badge
// ---------------------------------------------------------------------------

const VERDICT_COLORS: Record<string, string> = {
  DIVERGED: "red",
  MATCH: "green",
  SHADOW_FAILED: "gray",
  WITHIN_THRESHOLD: "yellow",
};

const VerdictBadge = ({ verdict }: { readonly verdict: string }) => (
  <Badge colorPalette={VERDICT_COLORS[verdict] ?? "gray"} variant="solid">
    {verdict}
  </Badge>
);

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  active: "cyan",
  cleaned_up: "gray",
  discarded: "gray",
  promoted: "green",
  registered: "blue",
  review: "yellow",
};

const StatusBadge = ({ status }: { readonly status: string }) => (
  <Badge colorPalette={STATUS_COLORS[status] ?? "gray"} variant="outline">
    {status}
  </Badge>
);

// ---------------------------------------------------------------------------
// Shadow row
// ---------------------------------------------------------------------------

const ShadowRow = ({ shadow }: { readonly shadow: ShadowDagRecord }) => {
  const { data: report, isLoading } = useQuery({
    queryFn: () => fetchLatestReport(shadow.shadow_id),
    queryKey: ["shadow-report", shadow.shadow_id],
    staleTime: 30_000,
  });

  return (
    <Table.Row>
      <Table.Cell fontFamily="mono" fontSize="sm">
        {shadow.shadow_id}
      </Table.Cell>
      <Table.Cell>{shadow.candidate_dag_id}</Table.Cell>
      <Table.Cell>
        <StatusBadge status={shadow.status} />
      </Table.Cell>
      <Table.Cell>
        {isLoading ? (
          <Spinner size="sm" />
        ) : report ? (
          <VerdictBadge verdict={report.verdict} />
        ) : (
          <Text color="fg.muted" fontSize="sm">
            No report yet
          </Text>
        )}
      </Table.Cell>
      <Table.Cell>
        {report ? `${report.row_count_delta_pct.toFixed(2)}%` : "—"}
      </Table.Cell>
      <Table.Cell>
        {report ? (
          <Text fontSize="sm">{report.row_count_shadow} / {report.row_count_prod}</Text>
        ) : (
          "—"
        )}
      </Table.Cell>
      <Table.Cell>
        {new Date(shadow.expires_at).toLocaleDateString()}
      </Table.Cell>
    </Table.Row>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const ShadowReports = () => {
  const { dagId = "" } = useParams();

  const { data, error, isLoading } = useQuery({
    queryFn: () => fetchShadowDags(dagId),
    queryKey: ["shadow-dags", dagId],
    staleTime: 60_000,
  });

  if (isLoading) {
    return (
      <Flex alignItems="center" justifyContent="center" p={8}>
        <Spinner />
      </Flex>
    );
  }

  if (error) {
    return (
      <Box p={4}>
        <Text color="fg.error">
          Failed to load Shadow DAG data: {(error as Error).message}
        </Text>
      </Box>
    );
  }

  const shadows = data?.shadow_dags ?? [];

  if (shadows.length === 0) {
    return (
      <Box p={6}>
        <Heading mb={2} size="md">
          Shadow Reports
        </Heading>
        <Text color="fg.muted">
          No Shadow DAG experiments are registered for this DAG.
        </Text>
        <Text color="fg.muted" fontSize="sm" mt={2}>
          To create one, run:{" "}
          <Text as="code" fontFamily="mono">
            airflow shadow create --production-dag {dagId} --candidate-dag-id &lt;dag_id&gt;
          </Text>
        </Text>
      </Box>
    );
  }

  return (
    <Box p={4}>
      <Heading mb={4} size="md">
        Shadow Reports
        <Text as="span" color="fg.muted" fontSize="sm" fontWeight="normal" ml={2}>
          ({shadows.length} experiment{shadows.length !== 1 ? "s" : ""})
        </Text>
      </Heading>

      <Table.Root size="sm" striped>
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader>Shadow ID</Table.ColumnHeader>
            <Table.ColumnHeader>Candidate DAG</Table.ColumnHeader>
            <Table.ColumnHeader>Status</Table.ColumnHeader>
            <Table.ColumnHeader>Latest Verdict</Table.ColumnHeader>
            <Table.ColumnHeader>Row Delta</Table.ColumnHeader>
            <Table.ColumnHeader>Shadow / Prod Rows</Table.ColumnHeader>
            <Table.ColumnHeader>Expires</Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {shadows.map((shadow) => (
            <ShadowRow key={shadow.shadow_id} shadow={shadow} />
          ))}
        </Table.Body>
      </Table.Root>
    </Box>
  );
};
