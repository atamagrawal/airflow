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
import { Box, Flex } from "@chakra-ui/react";

import { ProgressBar } from "src/components/ui";
import type { ReactNode } from "react";
import { useEffect } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";

import type { ExternalViewResponse, ReactAppResponse } from "openapi/requests/types.gen";
import { useColorMode } from "src/context/colorMode";
import { isReactAppPluginView } from "src/utils/pluginViewUtils";

import { Iframe } from "./Iframe";
import { ReactPlugin } from "./ReactPlugin";

type Member = ExternalViewResponse | ReactAppResponse;

const sortMembers = (members: Array<Member>): Array<Member> =>
  [...members].sort((left, right) =>
    left.name.localeCompare(right.name, undefined, { sensitivity: "base" }),
  );

const tabIcon = (
  view: Member,
  colorMode: "dark" | "light",
): ReactNode => {
  let iconSrc = view.icon;

  if (colorMode === "dark" && view.icon_dark_mode !== undefined && view.icon_dark_mode !== null) {
    iconSrc = view.icon_dark_mode;
  }

  return iconSrc !== undefined && iconSrc !== null ? (
    <img alt="" height={14} src={iconSrc} style={{ height: "14px", width: "14px" }} width={14} />
  ) : undefined;
};

type Props = {
  readonly members: Array<Member>;
  readonly selectedChildRoute: string;
};

export const GroupedPluginCategoryView = ({ members, selectedChildRoute }: Props) => {
  const { colorMode } = useColorMode();
  const navigate = useNavigate();
  const location = useLocation();
  const sorted = sortMembers(members);
  const [first] = sorted;
  const firstRoute = first?.url_route;

  useEffect(() => {
    if (sorted.length > 1 && selectedChildRoute === "" && firstRoute !== undefined && firstRoute !== null) {
      void Promise.resolve(navigate(firstRoute, { relative: "path", replace: true }));
    }
  }, [firstRoute, navigate, selectedChildRoute, sorted.length]);

  if (firstRoute === undefined || firstRoute === null) {
    return undefined;
  }

  if (sorted.length > 1 && selectedChildRoute === "") {
    return (
      <Box flexGrow={1} m={-2} minHeight={120} position="relative">
        <ProgressBar />
      </Box>
    );
  }

  const selected =
    selectedChildRoute === ""
      ? sorted.length === 1
        ? sorted[0]
        : undefined
      : sorted.find((member) => member.url_route === selectedChildRoute);

  if (selected === undefined) {
    return undefined;
  }

  const showSubNav = sorted.length > 1;

  return (
    <Box display="flex" flexDirection="column" flexGrow={1} height="100%" m={-2} minHeight={0}>
      {showSubNav ? (
        <Flex
          alignItems="center"
          borderBottomColor="border.emphasized"
          borderBottomWidth={1}
          flexWrap="wrap"
          gap={1}
          px={2}
          py={1}
        >
          {sorted.map((view) => {
            const route = view.url_route;

            if (route === undefined || route === null) {
              return undefined;
            }

            return (
              <NavLink
                end
                key={route}
                relative="path"
                style={{ textDecoration: "none" }}
                title={view.name}
                to={route}
              >
                {({ isActive }) => (
                  <Flex
                    alignItems="center"
                    borderBottomColor="border.info"
                    borderBottomWidth={isActive ? 2 : 0}
                    color={isActive ? "fg" : "fg.muted"}
                    fontWeight="semibold"
                    gap={1}
                    pb={isActive ? 0 : "2px"}
                    px={2}
                    py={1}
                  >
                    {tabIcon(view, colorMode ?? "light")}
                    {view.name}
                  </Flex>
                )}
              </NavLink>
            );
          })}
        </Flex>
      ) : undefined}
      <Box flexGrow={1} minHeight={0} overflow="hidden">
        {isReactAppPluginView(selected) ? (
          <ReactPlugin key={location.pathname} reactApp={selected} />
        ) : (
          <Iframe externalView={selected} sandbox="allow-scripts allow-same-origin allow-forms" />
        )}
      </Box>
    </Box>
  );
};
