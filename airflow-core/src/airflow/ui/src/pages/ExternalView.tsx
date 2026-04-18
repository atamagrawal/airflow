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
import { Box } from "@chakra-ui/react";
import { useTranslation } from "react-i18next";
import { useLocation, useParams } from "react-router-dom";

import { usePluginServiceGetPlugins } from "openapi/queries";
import type { ExternalViewResponse, ReactAppResponse } from "openapi/requests/types.gen";
import { ProgressBar } from "src/components/ui";
import { inferPluginDestinationFromPathname, slugifyPluginCategory } from "src/utils/pluginViewUtils";

import { ErrorPage } from "./Error";
import { GroupedPluginCategoryView } from "./GroupedPluginCategoryView";
import { Iframe } from "./Iframe";
import { ReactPlugin } from "./ReactPlugin";

type PluginTabView = ExternalViewResponse | ReactAppResponse;

const routeKeyForView = (view: PluginTabView): string =>
  view.url_route ?? view.name.toLowerCase().replaceAll(" ", "-");

export const ExternalView = () => {
  const { t: translate } = useTranslation();
  const params = useParams();
  const { page } = params;
  const splat = params["*"];
  const { data: pluginData, isLoading } = usePluginServiceGetPlugins();

  const { pathname } = useLocation();
  const childSegment = (splat ?? "").split("/").find((segment) => segment.length > 0) ?? "";

  const externalView =
    page === "legacy-fab-views"
      ? {
          destination: "nav" as const,
          href: "/pluginsv2/",
          name: translate("nav.legacyFabViews"),
          url_route: "legacy-fab-views",
        }
      : pluginData?.plugins
          .flatMap((plugin) => plugin.external_views)
          .find((view) => routeKeyForView(view) === page);

  const reactApp = pluginData?.plugins
    .flatMap((plugin) => plugin.react_apps)
    .find((view) => routeKeyForView(view) === page);

  const categoryMembers: Array<PluginTabView> | undefined = (() => {
    if (
      page === undefined ||
      page === "legacy-fab-views" ||
      pluginData === undefined ||
      externalView !== undefined ||
      reactApp !== undefined
    ) {
      return undefined;
    }

    const destination = inferPluginDestinationFromPathname(pathname);

    if (destination === undefined) {
      return undefined;
    }

    const members = pluginData.plugins
      .flatMap((plugin) => [...plugin.external_views, ...plugin.react_apps] as Array<PluginTabView>)
      .filter(
        (view) =>
          view.destination === destination &&
          Boolean(view.url_route) &&
          Boolean(view.category?.trim()) &&
          slugifyPluginCategory(view.category!.trim()) === page,
      );

    return members.length > 0 ? members : undefined;
  })();

  if (isLoading) {
    return (
      <Box flexGrow={1}>
        <ProgressBar />
      </Box>
    );
  }

  if (externalView) {
    return (
      <Box
        flexGrow={1}
        height="100%"
        m={-2} // Compensate for parent padding
        minHeight={0}
      >
        {/* The following iframe sandbox setting is intentionally less restrictive.
          This is considered safe because the framed content originates from the Plugins,
          which is part of the deployment of Airflow and trusted as per our security policy.
          https://airflow.apache.org/docs/apache-airflow/stable/security/security_model.html
          They are not user provided plugins. */}
        <Iframe externalView={externalView} sandbox="allow-scripts allow-same-origin allow-forms" />
      </Box>
    );
  }

  if (reactApp) {
    return (
      <Box
        flexGrow={1}
        height="100%"
        m={-2} // Compensate for parent padding
        minHeight={0}
      >
        <ReactPlugin key={pathname} reactApp={reactApp} />
      </Box>
    );
  }

  if (categoryMembers !== undefined) {
    return (
      <GroupedPluginCategoryView members={categoryMembers} selectedChildRoute={childSegment} />
    );
  }

  return <ErrorPage />;
};
