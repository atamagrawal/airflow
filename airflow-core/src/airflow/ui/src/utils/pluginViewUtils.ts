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
import type { ExternalViewResponse, ReactAppResponse } from "openapi/requests/types.gen";

export type PluginDestination = NonNullable<ExternalViewResponse["destination"]>;

export type PluginTabView = ExternalViewResponse | ReactAppResponse;

/** URL segment for grouped plugin tabs (must match server `category` strings, slugified). */
export const slugifyPluginCategory = (category: string): string => {
  const slug = category
    .trim()
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/gu, "-")
    .replaceAll(/^-+|-+$/gu, "");

  return slug.length > 0 ? slug : "plugin-category";
};

/**
 * Infer which plugin `destination` applies from the browser path prefix before `/plugin/`.
 */
export const inferPluginDestinationFromPathname = (pathname: string): PluginDestination | undefined => {
  const marker = "/plugin/";
  const markerIndex = pathname.indexOf(marker);

  if (markerIndex === -1) {
    return undefined;
  }

  const prefix = pathname.slice(0, markerIndex);

  if (
    /\/dags\/[^/]+\/runs\/[^/]+\/tasks\/(?:group\/)?[^/]+(?:\/mapped\/[^/]+)?$/u.test(prefix)
  ) {
    return "task_instance";
  }

  if (/\/dags\/[^/]+\/runs\/[^/]+$/u.test(prefix)) {
    return "dag_run";
  }

  if (/\/dags\/[^/]+\/tasks\/(?:group\/)?[^/]+$/u.test(prefix)) {
    return "task";
  }

  if (/\/dags\/[^/]+$/u.test(prefix)) {
    return "dag";
  }

  return "nav";
};

export const isReactAppPluginView = (view: PluginTabView): view is ReactAppResponse =>
  typeof (view as ReactAppResponse).bundle_url === "string";

export const getPluginTabUrlRoute = (view: PluginTabView): string | null | undefined => view.url_route;
