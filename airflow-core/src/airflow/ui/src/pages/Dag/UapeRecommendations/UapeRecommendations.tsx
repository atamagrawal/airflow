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
import { Badge, Box, Code, Heading, HStack, Skeleton, Table, Text, VStack } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import { FiAlertTriangle } from "react-icons/fi";
import { useParams } from "react-router-dom";

import { Alert } from "src/components/ui";
import { useUapeRecommendations } from "src/queries/useUapeRecommendations";
import type { AbstainedHint, OverlapHint, TaskClassification } from "src/queries/useUapeRecommendations";

const TaskClassificationsTable = ({
  classifications,
}: {
  readonly classifications: Array<TaskClassification>;
}) => {
  const { t: translate } = useTranslation("dag");

  return (
    <Box>
      <Heading mb={2} size="sm">
        {translate("uape.taskClassifications")}
      </Heading>
      <Table.Root size="sm" striped>
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader>{translate("uape.taskId")}</Table.ColumnHeader>
            <Table.ColumnHeader>{translate("uape.taskType")}</Table.ColumnHeader>
            <Table.ColumnHeader>{translate("uape.opacity")}</Table.ColumnHeader>
            <Table.ColumnHeader>{translate("uape.reason")}</Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {classifications.map((tc) => (
            <Table.Row key={tc.task_id}>
              <Table.Cell>
                <Code>{tc.task_id}</Code>
              </Table.Cell>
              <Table.Cell>{tc.task_type ?? "—"}</Table.Cell>
              <Table.Cell>
                <Badge colorPalette={tc.opacity === "clear" ? "green" : "orange"}>{tc.opacity}</Badge>
              </Table.Cell>
              <Table.Cell>
                <Text fontSize="xs">{tc.opacity_reason}</Text>
              </Table.Cell>
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </Box>
  );
};

const OverlapHintsTable = ({ hints }: { readonly hints: Array<OverlapHint> }) => {
  const { t: translate } = useTranslation("dag");

  if (hints.length === 0) {
    return (
      <Box>
        <Heading mb={2} size="sm">
          {translate("uape.overlapHints")}
        </Heading>
        <Text color="fg.muted" fontSize="sm">
          {translate("uape.noOverlapHints")}
        </Text>
      </Box>
    );
  }

  return (
    <Box>
      <Heading mb={2} size="sm">
        {translate("uape.overlapHints")}
      </Heading>
      <Table.Root size="sm" striped>
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader>{translate("uape.taskPair")}</Table.ColumnHeader>
            <Table.ColumnHeader>{translate("uape.proof")}</Table.ColumnHeader>
            <Table.ColumnHeader>{translate("uape.caveat")}</Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {hints.map((h) => (
            <Table.Row key={`${h.task_a}-${h.task_b}`}>
              <Table.Cell>
                <HStack gap={1}>
                  <Code>{h.task_a}</Code>
                  <Text>↔</Text>
                  <Code>{h.task_b}</Code>
                </HStack>
              </Table.Cell>
              <Table.Cell>
                <Text fontSize="xs">{h.proof.detail}</Text>
              </Table.Cell>
              <Table.Cell>
                <Text fontSize="xs">{h.caveat}</Text>
              </Table.Cell>
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </Box>
  );
};

const AbstentionsTable = ({ hints }: { readonly hints: Array<AbstainedHint> }) => {
  const { t: translate } = useTranslation("dag");

  if (hints.length === 0) {
    return undefined;
  }

  return (
    <Box>
      <Heading mb={2} size="sm">
        {translate("uape.abstentions")}
      </Heading>
      <Table.Root size="sm" striped>
        <Table.Header>
          <Table.Row>
            <Table.ColumnHeader>{translate("uape.taskPair")}</Table.ColumnHeader>
            <Table.ColumnHeader>{translate("uape.abstainReason")}</Table.ColumnHeader>
          </Table.Row>
        </Table.Header>
        <Table.Body>
          {hints.map((a) => (
            <Table.Row key={`${a.task_a}-${a.task_b}`}>
              <Table.Cell>
                <HStack gap={1}>
                  <Code>{a.task_a}</Code>
                  <Text>↔</Text>
                  <Code>{a.task_b}</Code>
                </HStack>
              </Table.Cell>
              <Table.Cell>
                <Text fontSize="xs">{a.abstain_reason}</Text>
              </Table.Cell>
            </Table.Row>
          ))}
        </Table.Body>
      </Table.Root>
    </Box>
  );
};

export const UapeRecommendations = () => {
  const { t: translate } = useTranslation("dag");
  const { dagId = "" } = useParams();
  const { data: report, error, isLoading } = useUapeRecommendations(dagId);

  if (isLoading) {
    return (
      <Box m={4} spaceY={4}>
        <Skeleton height="40px" width="full" />
        <Skeleton height="200px" width="full" />
        <Skeleton height="200px" width="full" />
      </Box>
    );
  }

  if (error) {
    return (
      <Box m={4}>
        <Alert status="warning" title={translate("uape.unavailableTitle")}>
          {translate("uape.unavailableDescription")}
        </Alert>
      </Box>
    );
  }

  if (!report) {
    return undefined;
  }

  return (
    <Box m={4} spaceY={4}>
      <Alert icon={<FiAlertTriangle />} status="info" title={translate("uape.advisoryTitle")}>
        {translate("uape.advisoryDescription")}
      </Alert>

      <HStack gap={4}>
        <Box>
          <Text fontSize="sm" fontWeight="bold">
            {translate("uape.policy")}
          </Text>
          <Badge>{report.policy}</Badge>
        </Box>
        <Box>
          <Text fontSize="sm" fontWeight="bold">
            {translate("uape.clearAllowlist")}
          </Text>
          <HStack gap={1}>
            {report.clear_operator_allowlist.map((op) => (
              <Badge colorPalette="green" key={op}>
                {op}
              </Badge>
            ))}
          </HStack>
        </Box>
      </HStack>

      <VStack align="stretch" gap={6}>
        <OverlapHintsTable hints={report.clear_task_overlap_hints} />
        <AbstentionsTable hints={report.abstained_parallel_hints} />
        <TaskClassificationsTable classifications={report.task_classifications} />
      </VStack>
    </Box>
  );
};
