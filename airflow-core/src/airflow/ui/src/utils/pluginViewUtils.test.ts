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
import { describe, expect, it } from "vitest";

import { inferPluginDestinationFromPathname, slugifyPluginCategory } from "src/utils/pluginViewUtils";

describe("slugifyPluginCategory", () => {
  it("slugifies categories for URL segments", () => {
    expect(slugifyPluginCategory("Recommendations")).toBe("recommendations");
    expect(slugifyPluginCategory("  Foo Bar  ")).toBe("foo-bar");
  });

  it("falls back when category is punctuation-only", () => {
    expect(slugifyPluginCategory("???")).toBe("plugin-category");
  });
});

describe("inferPluginDestinationFromPathname", () => {
  it("detects dag, dag_run, task, task_instance, and nav destinations", () => {
    expect(inferPluginDestinationFromPathname("/dags/d1/plugin/p")).toBe("dag");
    expect(inferPluginDestinationFromPathname("/dags/d1/runs/r1/plugin/p")).toBe("dag_run");
    expect(inferPluginDestinationFromPathname("/dags/d1/tasks/t1/plugin/p")).toBe("task");
    expect(inferPluginDestinationFromPathname("/dags/d1/runs/r1/tasks/t1/plugin/p")).toBe("task_instance");
    expect(
      inferPluginDestinationFromPathname("/dags/d1/runs/r1/tasks/t1/mapped/3/plugin/p"),
    ).toBe("task_instance");
    expect(inferPluginDestinationFromPathname("/dags/d1/runs/r1/tasks/group/g1/plugin/p")).toBe(
      "task_instance",
    );
    expect(inferPluginDestinationFromPathname("/plugin/p")).toBe("nav");
  });

  it("returns undefined when there is no plugin segment", () => {
    expect(inferPluginDestinationFromPathname("/dags/d1/runs/r1")).toBeUndefined();
  });
});
