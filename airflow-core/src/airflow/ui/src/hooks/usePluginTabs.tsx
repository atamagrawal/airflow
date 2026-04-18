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
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { LuPlug } from "react-icons/lu";

import { usePluginServiceGetPlugins } from "openapi/queries";
import type { ExternalViewResponse, ReactAppResponse } from "openapi/requests/types.gen";
import { useColorMode } from "src/context/colorMode";
import { slugifyPluginCategory } from "src/utils/pluginViewUtils";

type TabPlugin = {
  icon: ReactNode;
  label: string;
  navLinkEnd?: boolean;
  value: string;
};

type PluginTabView = ExternalViewResponse | ReactAppResponse;

export const usePluginTabs = (destination: string): Array<TabPlugin> => {
  const { t: translate } = useTranslation("dag");
  const { colorMode } = useColorMode();
  const { data: pluginData } = usePluginServiceGetPlugins();

  const externalViews =
    pluginData?.plugins
      .flatMap((plugin) => [...plugin.external_views, ...plugin.react_apps])
      .filter(
        (view: PluginTabView) => view.destination === destination && Boolean(view.url_route),
      ) ?? [];

  const emittedCategorySlugs = new Set<string>();
  const categoryMembers = new Map<string, Array<PluginTabView>>();

  for (const view of externalViews) {
    const rawCategory = view.category?.trim();

    if (rawCategory !== undefined && rawCategory !== "") {
      const slug = slugifyPluginCategory(rawCategory);
      const bucket = categoryMembers.get(slug);

      if (bucket === undefined) {
        categoryMembers.set(slug, [view]);
      } else {
        bucket.push(view);
      }
    }
  }

  const tabForView = (view: PluginTabView): TabPlugin => {
    let iconSrc = view.icon;

    if (colorMode === "dark" && view.icon_dark_mode !== undefined && view.icon_dark_mode !== null) {
      iconSrc = view.icon_dark_mode;
    }

    const icon =
      iconSrc !== undefined && iconSrc !== null ? (
        <img alt={view.name} src={iconSrc} style={{ height: "1rem", width: "1rem" }} />
      ) : (
        <LuPlug />
      );

    return {
      icon,
      label: view.name,
      navLinkEnd: true,
      value: `plugin/${view.url_route ?? ""}`,
    };
  };

  const tabForCategory = (slug: string): TabPlugin => {
    const members = categoryMembers.get(slug) ?? [];
    const sorted = [...members].sort((left, right) =>
      left.name.localeCompare(right.name, undefined, { sensitivity: "base" }),
    );
    const [primary] = sorted;

    if (primary === undefined) {
      return {
        icon: <LuPlug />,
        label: translate(`tabs.pluginCategory.${slug}`, { defaultValue: slug.replaceAll("-", " ") }),
        navLinkEnd: true,
        value: `plugin/${slug}`,
      };
    }

    let iconSrc = primary.icon;

    if (colorMode === "dark" && primary.icon_dark_mode !== undefined && primary.icon_dark_mode !== null) {
      iconSrc = primary.icon_dark_mode;
    }

    const icon =
      iconSrc !== undefined && iconSrc !== null ? (
        <img alt="" src={iconSrc} style={{ height: "1rem", width: "1rem" }} />
      ) : (
        <LuPlug />
      );

    const labelKey = `tabs.pluginCategory.${slug}`;

    return {
      icon,
      label: translate(labelKey, { defaultValue: slug.replaceAll("-", " ") }),
      navLinkEnd: sorted.length <= 1,
      value: `plugin/${slug}`,
    };
  };

  const tabs: Array<TabPlugin> = [];

  for (const view of externalViews) {
    const rawCategory = view.category?.trim();

    if (rawCategory === undefined || rawCategory === "") {
      tabs.push(tabForView(view));
    } else {
      const slug = slugifyPluginCategory(rawCategory);

      if (!emittedCategorySlugs.has(slug)) {
        emittedCategorySlugs.add(slug);
        tabs.push(tabForCategory(slug));
      }
    }
  }

  return tabs;
};
