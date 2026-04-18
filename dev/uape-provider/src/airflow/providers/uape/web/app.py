# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

from __future__ import annotations

import html
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from airflow.exceptions import AirflowException
from airflow.models.serialized_dag import SerializedDagModel
from airflow.providers.uape.parallelization import analyze_serialized_dag
from airflow.utils.session import create_session


def _load_report_or_raise(dag_id: str) -> dict[str, Any]:
    with create_session() as session:
        row = SerializedDagModel.get(dag_id, session=session)
    if row is None:
        raise AirflowException(
            f"No serialized DAG found for {dag_id!r}. Ensure the DAG is parsed and serialization is enabled."
        )
    return analyze_serialized_dag(row.dag)


def _recommendations_ui_page(dag_id: str) -> str:
    """HTML/JS for the Airflow plugin iframe: list + detail with search (no external assets)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Recommendations · {html.escape(dag_id, quote=True)}</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #fff;
      --border: #e2e5eb;
      --text: #1a1d26;
      --muted: #5c6578;
      --accent: #0b57d0;
      --accent-soft: #e8f0fe;
      --pill-overview-bg: #eceff1; --pill-overview-fg: #5f6368;
      --pill-parallel-bg: #e6f4ea; --pill-parallel-fg: #137333;
      --pill-abstain-bg: #fef7e0; --pill-abstain-fg: #b06000;
      --pill-class-bg: #e8f0fe;   --pill-class-fg: #1967d2;
      --item-bg: #fafbfc;
      --code-bg: #f3f4f6;
      --th-bg: #f3f4f6;
      --active-border: #a8c7fa;
    }}
    :root.dark {{
      --bg: #0f1117;
      --panel: #1a1d26;
      --border: #2d3142;
      --text: #e8eaf0;
      --muted: #8b92a9;
      --accent: #7baaf7;
      --accent-soft: #1a2540;
      --pill-overview-bg: #2a2d3a; --pill-overview-fg: #9aa0b8;
      --pill-parallel-bg: #0d2b1a; --pill-parallel-fg: #57bb7e;
      --pill-abstain-bg: #2b1f05; --pill-abstain-fg: #e8a030;
      --pill-class-bg: #0d1f40;   --pill-class-fg: #7baaf7;
      --item-bg: #1e2232;
      --code-bg: #252839;
      --th-bg: #252839;
      --active-border: #4a7fd4;
    }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; color: var(--text);
      line-height: 1.5; height: 100vh; display: flex; flex-direction: column; background: var(--bg); }}
    header {{ padding: 0.5rem 1rem; background: var(--panel); border-bottom: 1px solid var(--border);
      flex-shrink: 0; }}
    .sub {{ font-size: 0.8rem; color: var(--muted); margin: 0; }}
    .shell {{ display: flex; flex: 1; min-height: 0; gap: 0; }}
    .list-pane {{ width: min(340px, 42vw); min-width: 220px; background: var(--panel);
      border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }}
    .list-head {{ padding: 0.65rem 0.75rem; border-bottom: 1px solid var(--border); flex-shrink: 0; }}
    .list-head h2 {{ margin: 0; font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--muted); }}
    .list-head .count {{ font-size: 0.85rem; font-weight: 500; color: var(--text); margin-top: 0.2rem; }}
    .search {{ width: 100%; margin-top: 0.45rem; padding: 0.45rem 0.55rem; border: 1px solid var(--border);
      border-radius: 8px; font: inherit; font-size: 0.85rem;
      background: var(--bg); color: var(--text); }}
    .search::placeholder {{ color: var(--muted); }}
    .search:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; border-color: var(--accent); }}
    .list-scroll {{ flex: 1; overflow-y: auto; padding: 0.4rem 0.5rem 0.75rem; }}
    .list-item {{ display: block; width: 100%; text-align: left; padding: 0.55rem 0.6rem; margin-bottom: 0.35rem;
      border: 1px solid var(--border); border-radius: 10px; background: var(--item-bg); cursor: pointer;
      font: inherit; transition: box-shadow 0.12s, border-color 0.12s, background 0.12s; }}
    .list-item:hover {{ background: var(--panel); border-color: var(--border); box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
    .list-item:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
    .list-item.active {{ background: var(--accent-soft); border-color: var(--active-border); box-shadow: 0 0 0 1px var(--active-border); }}
    .list-item .row1 {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 0.35rem; }}
    .pill {{ font-size: 0.65rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
      padding: 0.12rem 0.38rem; border-radius: 999px; flex-shrink: 0; white-space: nowrap; }}
    .pill-overview {{ background: var(--pill-overview-bg); color: var(--pill-overview-fg); }}
    .pill-parallel {{ background: var(--pill-parallel-bg); color: var(--pill-parallel-fg); }}
    .pill-abstain {{ background: var(--pill-abstain-bg); color: var(--pill-abstain-fg); }}
    .pill-class {{ background: var(--pill-class-bg); color: var(--pill-class-fg); }}
    .list-title {{ font-size: 0.88rem; font-weight: 600; color: var(--text); margin-top: 0.25rem;
      word-break: break-word; }}
    .list-preview {{ font-size: 0.75rem; color: var(--muted); margin-top: 0.2rem; display: -webkit-box;
      -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .chev {{ color: #9aa0a9; font-size: 1rem; line-height: 1; margin-left: 0.25rem; }}
    .detail-pane {{ flex: 1; overflow: auto; background: var(--bg); padding: 1rem 1.1rem; }}
    .detail-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
      padding: 1rem 1.1rem; max-width: 56rem; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .detail-card h2 {{ margin: 0 0 0.65rem 0; font-size: 1.05rem; }}
    .detail-meta {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 0.75rem; }}
    .empty-list {{ padding: 1rem; text-align: center; color: var(--muted); font-size: 0.85rem; }}
    .err {{ color: #b3261e; padding: 0.75rem 1rem; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
    th, td {{ border: 1px solid var(--border); padding: 0.4rem 0.55rem; text-align: left; vertical-align: top; }}
    th {{ background: var(--th-bg); font-weight: 600; }}
    code {{ font-size: 0.88em; background: var(--code-bg); padding: 0.08rem 0.28rem; border-radius: 4px; }}
    #status {{ padding: 0.65rem 1rem; margin: 0; }}
    details {{ color: var(--text); }}
    summary {{ color: var(--muted); cursor: pointer; }}
  </style>
</head>
<body>
  <header>
    <p class="sub">DAG <strong>{html.escape(dag_id, quote=True)}</strong></p>
  </header>
  <p id="status">Loading…</p>
  <div id="root" class="shell" style="display:none" role="presentation"></div>
  <script>
    (function () {{
      // Theme sync: prefer ?theme= URL param (set by Airflow Iframe.tsx if available),
      // fall back to reading the parent document's <html> class (same-origin, works always).
      function applyTheme(dark) {{
        document.documentElement.classList.toggle("dark", dark);
      }}
      function isDarkParent() {{
        try {{
          return window.parent.document.documentElement.classList.contains("dark");
        }} catch (_) {{
          return false;
        }}
      }}
      const param = new URLSearchParams(location.search).get("theme");
      if (param !== null) {{
        applyTheme(param === "dark");
      }} else {{
        applyTheme(isDarkParent());
      }}
      // Watch parent <html> for class changes (live theme toggle, same-origin only).
      try {{
        new MutationObserver(function () {{
          applyTheme(isDarkParent());
        }}).observe(window.parent.document.documentElement, {{ attributes: true, attributeFilter: ["class"] }});
      }} catch (_) {{}}
    }})();
    const status = document.getElementById("status");
    const root = document.getElementById("root");
    function esc(s) {{
      const d = document.createElement("div");
      d.textContent = s;
      return d.innerHTML;
    }}
    function clip(s, n) {{
      const t = (s || "").trim();
      if (t.length <= n) return t;
      return t.slice(0, n - 1) + "…";
    }}
    function stepsHtml(steps) {{
      if (!steps || !steps.length) return "";
      let out = "<div style='margin-top:1rem'>"
        + "<p style='font-size:0.8rem;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;"
        + "color:var(--muted);margin:0 0 0.4rem'>Next steps</p>"
        + "<ol style='margin:0;padding-left:1.25rem'>";
      for (const s of steps) {{
        out += "<li style='margin-bottom:0.3rem'>" + esc(s) + "</li>";
      }}
      return out + "</ol></div>";
    }}
    const TIER_LABELS = {{
      t1_trivial:       {{ badge: "T1 · trivial",       color: "#137333", bg: "#e6f4ea" }},
      t2_computation:   {{ badge: "T2 · computation",   color: "#1967d2", bg: "#e8f0fe" }},
      t3_user_extended: {{ badge: "T3 · user-extended", color: "#b06000", bg: "#fef7e0" }},
    }};
    const CONF_LABELS = {{ high: "high", medium_high: "medium-high", medium: "medium", low: "low" }};
    function tierBadge(tier) {{
      if (!tier || !TIER_LABELS[tier]) return "";
      const info = TIER_LABELS[tier];
      return "<span style='display:inline-block;font-size:0.65rem;font-weight:700;text-transform:uppercase;"
        + "letter-spacing:0.04em;padding:0.1rem 0.4rem;border-radius:999px;background:" + info.bg + ";color:" + info.color + ";margin-right:0.2rem'>"
        + esc(info.badge) + "</span>";
    }}
    function hintDetailHtml(h, diagFooter) {{
      const pair = "<code>" + esc(h.task_a || "?") + "</code> and <code>" + esc(h.task_b || "?") + "</code>";
      const confLabel = CONF_LABELS[h.confidence] || h.confidence || "";
      const tierRow = (h.task_a_clear_tier || h.task_b_clear_tier)
        ? "<p style='margin:0 0 0.6rem;font-size:0.82rem;color:var(--muted)'>"
            + "Confidence: <strong>" + esc(confLabel) + "</strong> &nbsp;·&nbsp; "
            + "Tiers: " + tierBadge(h.task_a_clear_tier) + esc(h.task_a || "?")
            + " &nbsp;+&nbsp; "
            + tierBadge(h.task_b_clear_tier) + esc(h.task_b || "?")
            + "</p>"
        : "";
      return "<p style='font-size:1.05rem;font-weight:600;margin:0 0 0.5rem'>"
        + esc(h.recommendation_summary || (h.task_a + " and " + h.task_b + " can run in parallel.")) + "</p>"
        + tierRow
        + "<p style='margin:0 0 1rem;color:var(--muted);font-size:0.85rem'>"
        + "The DAG declares no dependency between " + pair + ","
        + " so the scheduler can start both as soon as their upstream tasks finish.</p>"
        + (h.caveat
          ? "<div style='padding:0.6rem 0.75rem;background:var(--accent-soft);border-radius:8px;"
            + "border:1px solid var(--active-border);font-size:0.85rem;margin-bottom:0.75rem'>"
            + "<strong>⚠ Watch out:</strong> " + esc(h.caveat) + "</div>"
          : "")
        + stepsHtml(h.suggested_next_steps)
        + diagFooter;
    }}
    function tableClassifications(cls) {{
      if (!cls.length) return "";
      const cap = 120;
      const slice = cls.slice(0, cap);
      let body = "";
      for (const t of slice) {{
        const tierCell = t.clear_tier ? tierBadge(t.clear_tier) : "<span style='color:var(--muted)'>—</span>";
        body += "<tr><td><code>" + esc(t.task_id) + "</code></td><td>" + esc(String(t.task_type || "—")) + "</td>"
          + "<td>" + esc(t.opacity || "") + "</td><td>" + tierCell + "</td><td>" + esc(t.opacity_reason || "") + "</td></tr>";
      }}
      const more = cls.length > cap ? "<p class='detail-meta'>Showing " + cap + " of " + cls.length + " rows.</p>" : "";
      return "<details style='margin-top:1rem'><summary><strong>Task opacity (reference)</strong></summary>"
        + "<p class='detail-meta'>Used to decide which operators count as structurally &quot;clear&quot;.</p>"
        + "<table><thead><tr><th>Task</th><th>Type</th><th>Opacity</th><th>Tier</th><th>Reason</th></tr></thead><tbody>"
        + body + "</tbody></table>" + more + "</details>";
    }}
    function tableAbstentions(abst, total) {{
      if (!abst.length) return "";
      const cap = 80;
      const slice = abst.slice(0, cap);
      let body = "";
      for (const a of slice) {{
        body += "<tr><td><code>" + esc(a.task_a) + "</code> ↔ <code>" + esc(a.task_b) + "</code></td>"
          + "<td>" + esc(a.abstain_reason || "") + "</td></tr>";
      }}
      const tot = total != null ? total : abst.length;
      const more = tot > slice.length
        ? "<p class='detail-meta'>Showing " + slice.length + " of " + tot + " opaque-involved independent pair(s).</p>"
        : "";
      return "<details style='margin-top:1rem'><summary><strong>Independent pairs involving opaque tasks (reference)</strong></summary>"
        + "<p class='detail-meta'>These are <em>not</em> parallel hints—this analysis does not suggest running them together.</p>"
        + "<table><thead><tr><th>Tasks</th><th>Why not a parallel hint</th></tr></thead><tbody>"
        + body + "</tbody></table>" + more + "</details>";
    }}
    fetch("recommendations.json", {{ credentials: "include" }})
      .then((r) => (r.ok ? r.json() : r.json().then((j) => Promise.reject(j.error || r.statusText))))
      .then((data) => {{
        status.textContent = "";
        root.style.display = "flex";
        const hints = data.clear_task_overlap_hints || [];
        const abst = data.abstained_parallel_hints || [];
        const cls = data.task_classifications || [];
        const items = [];
        const gm = data.graph_metrics || {{}};
        const lim = data.analysis_limits || {{}};
        const abstTotal = data.abstained_parallel_hints_total != null
          ? data.abstained_parallel_hints_total
          : abst.length;
        // Build a compact diagnostics footer shown at the bottom of every detail card.
        let scopeNote = "";
        if (lim.full_independent_pair_analysis === false) {{
          scopeNote = " · " + esc(lim.note || lim.reason || "limited analysis");
        }} else if (lim.abstentions_capped) {{
          scopeNote = " · abstentions capped at " + esc(String(lim.abstentions_cap || ""))
            + " of " + esc(String(abstTotal));
        }}
        const metricsStr = gm.task_count != null
          ? esc(String(gm.task_count)) + " tasks · " + esc(String(gm.dependency_edge_count))
            + " edges · " + esc(String(gm.clear_task_count)) + " clear / "
            + esc(String(gm.opaque_task_count)) + " opaque"
          : "";
        const tiers = data.clear_operator_allowlist_tiers || {{}};
        const tierSummary = [
          tiers.t1_trivial && tiers.t1_trivial.length ? "T1: " + tiers.t1_trivial.map(esc).join(", ") : "",
          tiers.t2_computation && tiers.t2_computation.length ? "T2: " + tiers.t2_computation.map(esc).join(", ") : "",
          tiers.t3_user_extended && tiers.t3_user_extended.length ? "T3 (user): " + tiers.t3_user_extended.map(esc).join(", ") : "",
        ].filter(Boolean).join(" · ");
        const diagFooter = "<details style='margin-top:1.25rem'>"
          + "<summary style='font-size:0.75rem;color:var(--muted);cursor:pointer'>Analysis details</summary>"
          + "<div style='font-size:0.8rem;color:var(--muted);margin-top:0.5rem'>"
          + "<p>Source: UAPE · policy: " + esc(data.policy || "—") + "</p>"
          + (tierSummary ? "<p>Allowlist · " + tierSummary + "</p>" : "")
          + (metricsStr ? "<p>" + metricsStr + scopeNote + "</p>" : "")
          + (data.generated_at_utc ? "<p>Generated " + esc(data.generated_at_utc) + " · provider " + esc(data.uape_provider_version || "—") + "</p>" : "")
          + "</div>"
          + tableAbstentions(abst, abstTotal)
          + tableClassifications(cls)
          + "</details>";
        let hi = 0;
        for (const h of hints) {{
          const id = "hint-" + (hi++);
          const pairLabel = (h.task_a || "?") + " + " + (h.task_b || "?");
          const summary = h.recommendation_summary || (h.task_a + " and " + h.task_b + " can run in parallel.");
          items.push({{
            id: id,
            kind: "parallel",
            title: pairLabel,
            preview: clip(summary, 110),
            detailTitle: pairLabel,
            detailKind: "Parallel overlap · UAPE",
            searchText: (pairLabel + " " + summary + " " + (h.caveat || "")
              + " " + (h.suggested_next_steps || []).join(" ")).toLowerCase(),
            html: hintDetailHtml(h, diagFooter)
          }});
        }}
        if (hints.length === 0) {{
          items.push({{
            id: "no-hints",
            kind: "parallel",
            title: "No parallel opportunities found",
            preview: "All tasks either depend on each other or use operator types this analysis can't assess.",
            detailTitle: "No parallel opportunities found",
            detailKind: "Parallel overlap · UAPE",
            searchText: "parallel overlap no opportunities",
            html: "<p style='font-size:1.05rem;font-weight:600;margin:0 0 0.5rem'>Nothing to suggest right now.</p>"
              + "<p style='color:var(--muted);font-size:0.85rem'>Every task pair in this DAG either has a dependency "
              + "between them, or uses an operator type that this analysis can't confidently assess. "
              + "That's expected for most DAGs — it doesn't mean something is wrong.</p>"
              + diagFooter
          }});
        }}
        const listPane = document.createElement("div");
        listPane.className = "list-pane";
        listPane.innerHTML = ""
          + '<div class="list-head">'
          + '<h2>Recommendations</h2>'
          + "<div class='count'>" + hints.length + " parallel overlap hint(s)</div>"
          + '<input type="search" id="q" class="search" placeholder="Filter…" '
          + 'autocomplete="off" aria-label="Filter recommendations" />'
          + "</div>"
          + '<div class="list-scroll" id="listMount"></div>';
        const searchInput = listPane.querySelector("#q");
        const listMount = listPane.querySelector("#listMount");
        const detailPane = document.createElement("div");
        detailPane.className = "detail-pane";
        const detailInner = document.createElement("div");
        detailInner.className = "detail-card";
        detailPane.appendChild(detailInner);
        let activeId = items[0].id;
        function pillClass(kind) {{
          if (kind === "parallel") return "pill pill-parallel";
          if (kind === "abstain") return "pill pill-abstain";
          return "pill pill-class";
        }}
        function pillLabel(kind) {{
          return "Parallel overlap";
        }}
        function renderList() {{
          const q = (searchInput.value || "").trim().toLowerCase();
          listMount.innerHTML = "";
          const filtered = q ? items.filter((it) => it.searchText.includes(q)) : items;
          if (filtered.length === 0) {{
            const empty = document.createElement("div");
            empty.className = "empty-list";
            empty.textContent = "No items match your filter.";
            listMount.appendChild(empty);
            detailInner.innerHTML = "<h2>No matches</h2><p class='detail-meta'>Try a shorter or different search.</p>";
            return;
          }}
          for (const it of filtered) {{
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "list-item" + (it.id === activeId ? " active" : "");
            btn.dataset.id = it.id;
            const pill = document.createElement("span");
            pill.className = pillClass(it.kind);
            pill.textContent = pillLabel(it.kind);
            const chev = document.createElement("span");
            chev.className = "chev";
            chev.setAttribute("aria-hidden", "true");
            chev.textContent = "›";
            const row1 = document.createElement("div");
            row1.className = "row1";
            row1.appendChild(pill);
            row1.appendChild(chev);
            const title = document.createElement("div");
            title.className = "list-title";
            title.textContent = it.title;
            const preview = document.createElement("div");
            preview.className = "list-preview";
            preview.textContent = it.preview;
            btn.appendChild(row1);
            btn.appendChild(title);
            btn.appendChild(preview);
            btn.addEventListener("click", () => {{
              activeId = it.id;
              renderList();
              showDetail();
            }});
            listMount.appendChild(btn);
          }}
        }}
        function showDetail() {{
          const cur = items.find((x) => x.id === activeId);
          if (!cur) return;
          detailInner.innerHTML = "<p class='detail-meta'><strong>" + esc(cur.detailKind) + "</strong></p>"
            + "<h2>" + esc(cur.detailTitle) + "</h2>" + cur.html;
        }}
        searchInput.addEventListener("input", () => {{
          const q = (searchInput.value || "").trim().toLowerCase();
          const filtered = q ? items.filter((it) => it.searchText.includes(q)) : items;
          if (filtered.length > 0 && !filtered.some((it) => it.id === activeId)) {{
            activeId = filtered[0].id;
          }}
          renderList();
          if (filtered.length > 0) {{
            showDetail();
          }}
        }});
        renderList();
        showDetail();
        root.appendChild(listPane);
        root.appendChild(detailPane);
      }})
      .catch((e) => {{
        status.innerHTML = '<p class="err">' + esc(String(e)) + "</p>";
      }});
  </script>
</body>
</html>
"""


def create_uape_app() -> FastAPI:
    """FastAPI sub-application mounted under ``/uape`` on the API server."""
    app = FastAPI(
        title="UAPE advisory API",
        description="Read-only parallel overlap diagnostics and hints; JSON API and a small HTML page for the Airflow UI plugin tab.",
        version="0.0.1",
        docs_url=None,
        redoc_url=None,
    )

    @app.get("/dags/{dag_id}/recommendations-ui", response_class=HTMLResponse)
    def recommendations_ui(dag_id: str) -> HTMLResponse:
        return HTMLResponse(_recommendations_ui_page(dag_id))

    @app.get("/dags/{dag_id}/recommendations.json")
    def recommendations_json(dag_id: str) -> JSONResponse:
        try:
            report = _load_report_or_raise(dag_id)
        except AirflowException as e:
            return JSONResponse(status_code=404, content={"error": str(e)})
        return JSONResponse(report)

    return app
