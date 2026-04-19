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

import hashlib
import html
import json
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import String, Text, func, select
from sqlalchemy.orm import Mapped, mapped_column

from airflow.exceptions import AirflowException
from airflow.models.base import Base
from airflow.models.dagrun import DagRun
from airflow.models.serialized_dag import SerializedDagModel
from airflow.providers.uape.parallelization import analyze_dag_edges
from airflow.utils.session import create_session
from airflow.utils.sqlalchemy import UtcDateTime


class UapeReportCache(Base):
    """DB-backed cache for UAPE analysis results.

    Keyed by ``dag_id``; invalidated whenever the serialised DAG or the set of
    DagRuns changes (tracked via a SHA-256 of the two source timestamps).  The
    table is created automatically on first use — no Alembic migration needed
    for this experimental provider.
    """

    __tablename__ = "uape_report_cache"

    dag_id: Mapped[str] = mapped_column(String(250), primary_key=True)
    # SHA-256 hex digest of (dag_last_updated, latest_run_start) — cheap to
    # compare and immune to datetime timezone representation differences.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    cached_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


def _content_hash(dag_last_updated: datetime | None, latest_run_start: datetime | None) -> str:
    raw = f"{dag_last_updated!r}|{latest_run_start!r}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _ensure_cache_table() -> None:
    """Create the cache table if it does not already exist."""
    from airflow.settings import engine

    UapeReportCache.__table__.create(engine, checkfirst=True)


def _load_report(dag_id: str) -> dict[str, Any]:
    with create_session() as session:
        row = SerializedDagModel.get(dag_id, session=session)
        if row is None:
            raise AirflowException(
                f"No serialized DAG found for {dag_id!r}. "
                f"Ensure the DAG is parsed and serialization is enabled."
            )

        # Cheap scalar: most recent DagRun start_date for this DAG.
        latest_run_start: datetime | None = session.scalar(
            select(func.max(DagRun.start_date)).where(DagRun.dag_id == dag_id)
        )

        wanted_hash = _content_hash(row.last_updated, latest_run_start)

        cached: UapeReportCache | None = session.get(UapeReportCache, dag_id)
        if cached is not None and cached.content_hash == wanted_hash:
            return json.loads(cached.report_json)

        # Cache miss — run the full analysis.
        report = analyze_dag_edges(row.dag, session=session)
        report_json = json.dumps(report, default=str)

        if cached is None:
            cached = UapeReportCache(dag_id=dag_id)
            session.add(cached)

        cached.content_hash = wanted_hash
        cached.report_json = report_json
        cached.cached_at = row.last_updated  # use DAG timestamp as a human-readable marker
        # session.commit() is NOT called here — create_session() commits on exit.
        return report


def _recommendations_ui_page(dag_id: str) -> str:
    safe_dag_id = html.escape(dag_id, quote=True)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>UAPE · {safe_dag_id}</title>
  <style>
    :root {{
      --bg: #f6f7f9; --panel: #fff; --border: #e2e5eb; --text: #1a1d26;
      --muted: #5c6578; --accent: #0b57d0; --accent-soft: #e8f0fe;
      --pill-remove-bg: #fde8e8; --pill-remove-fg: #b3261e;
      --pill-uncertain-bg: #fef7e0; --pill-uncertain-fg: #b06000;
      --pill-keep-bg: #e6f4ea; --pill-keep-fg: #137333;
      --item-bg: #fafbfc; --code-bg: #f3f4f6; --th-bg: #f3f4f6;
      --active-border: #a8c7fa; --pass-fg: #137333; --fail-fg: #b3261e;
      --skip-fg: #8b92a9;
    }}
    :root.dark {{
      --bg: #0f1117; --panel: #1a1d26; --border: #2d3142; --text: #e8eaf0;
      --muted: #8b92a9; --accent: #7baaf7; --accent-soft: #1a2540;
      --pill-remove-bg: #2b0a0a; --pill-remove-fg: #f28b82;
      --pill-uncertain-bg: #2b1f05; --pill-uncertain-fg: #e8a030;
      --pill-keep-bg: #0d2b1a; --pill-keep-fg: #57bb7e;
      --item-bg: #1e2232; --code-bg: #252839; --th-bg: #252839;
      --active-border: #4a7fd4; --pass-fg: #57bb7e; --fail-fg: #f28b82;
      --skip-fg: #5c6578;
    }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 0; color: var(--text);
      line-height: 1.5; height: 100vh; display: flex; flex-direction: column; background: var(--bg); }}
    header {{ padding: 0.5rem 1rem; background: var(--panel); border-bottom: 1px solid var(--border); flex-shrink: 0; }}
    .shell {{ display: flex; flex: 1; min-height: 0; }}
    .list-pane {{ width: min(340px, 42vw); min-width: 220px; background: var(--panel);
      border-right: 1px solid var(--border); display: flex; flex-direction: column; flex-shrink: 0; }}
    .list-head {{ padding: 0.65rem 0.75rem; border-bottom: 1px solid var(--border); flex-shrink: 0; }}
    .list-head h2 {{ margin: 0; font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
      letter-spacing: 0.06em; color: var(--muted); }}
    .count {{ font-size: 0.85rem; font-weight: 500; color: var(--text); margin-top: 0.2rem; }}
    .search {{ width: 100%; margin-top: 0.45rem; padding: 0.45rem 0.55rem; border: 1px solid var(--border);
      border-radius: 8px; font: inherit; font-size: 0.85rem; background: var(--bg); color: var(--text); }}
    .search:focus {{ outline: 2px solid var(--accent); border-color: var(--accent); }}
    .list-scroll {{ flex: 1; overflow-y: auto; padding: 0.4rem 0.5rem 0.75rem; }}
    .list-item {{ display: block; width: 100%; text-align: left; padding: 0.55rem 0.6rem;
      margin-bottom: 0.35rem; border: 1px solid var(--border); border-radius: 10px;
      background: var(--item-bg); cursor: pointer; font: inherit;
      transition: box-shadow 0.12s, border-color 0.12s; }}
    .list-item:hover {{ background: var(--panel); box-shadow: 0 1px 3px rgba(0,0,0,0.06); }}
    .list-item.active {{ background: var(--accent-soft); border-color: var(--active-border);
      box-shadow: 0 0 0 1px var(--active-border); }}
    .row1 {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 0.35rem; }}
    .pill {{ font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
      padding: 0.12rem 0.38rem; border-radius: 999px; flex-shrink: 0; white-space: nowrap; }}
    .pill-remove {{ background: var(--pill-remove-bg); color: var(--pill-remove-fg); }}
    .pill-uncertain {{ background: var(--pill-uncertain-bg); color: var(--pill-uncertain-fg); }}
    .pill-keep {{ background: var(--pill-keep-bg); color: var(--pill-keep-fg); }}
    .list-title {{ font-size: 0.88rem; font-weight: 600; margin-top: 0.25rem; word-break: break-word; }}
    .list-preview {{ font-size: 0.75rem; color: var(--muted); margin-top: 0.2rem;
      display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
    .chev {{ color: var(--muted); font-size: 1rem; margin-left: 0.25rem; }}
    .detail-pane {{ flex: 1; overflow: auto; background: var(--bg); padding: 1rem 1.1rem; }}
    .detail-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
      padding: 1rem 1.1rem; max-width: 58rem; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .detail-card h2 {{ margin: 0 0 0.5rem; font-size: 1.05rem; }}
    .detail-meta {{ font-size: 0.8rem; color: var(--muted); margin-bottom: 0.75rem; }}
    .score-bar {{ height: 8px; border-radius: 999px; background: var(--border); margin-bottom: 0.75rem; overflow: hidden; }}
    .score-fill {{ height: 100%; border-radius: 999px; transition: width 0.3s; }}
    .signal-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-bottom: 0.75rem; }}
    .signal-table th, .signal-table td {{ border: 1px solid var(--border); padding: 0.38rem 0.55rem; text-align: left; vertical-align: top; }}
    .signal-table th {{ background: var(--th-bg); font-weight: 600; }}
    .pass {{ color: var(--pass-fg); font-weight: 600; }}
    .fail {{ color: var(--fail-fg); font-weight: 600; }}
    .skip {{ color: var(--skip-fg); }}
    .savings-box {{ padding: 0.65rem 0.85rem; background: var(--accent-soft);
      border: 1px solid var(--active-border); border-radius: 8px; font-size: 0.85rem;
      margin-bottom: 0.75rem; }}
    .diff-box {{ font-size: 0.82rem; font-family: ui-monospace, monospace; background: var(--code-bg);
      padding: 0.65rem 0.85rem; border-radius: 8px; border: 1px solid var(--border);
      white-space: pre-wrap; margin-bottom: 0.75rem; }}
    .suggestion-box {{ padding: 0.6rem 0.75rem; background: var(--pill-uncertain-bg);
      border: 1px solid var(--border); border-radius: 8px; font-size: 0.85rem; margin-bottom: 0.75rem; }}
    code {{ font-size: 0.88em; background: var(--code-bg); padding: 0.08rem 0.28rem; border-radius: 4px; }}
    .empty-list, #status {{ padding: 0.75rem 1rem; color: var(--muted); font-size: 0.9rem; }}
    .err {{ color: var(--pill-remove-fg); padding: 0.75rem 1rem; }}
    details {{ color: var(--text); margin-top: 0.75rem; }}
    summary {{ color: var(--muted); cursor: pointer; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <header>
    <p style="margin:0;font-size:0.8rem;color:var(--muted)">
      UAPE · DAG <strong>{safe_dag_id}</strong>
    </p>
  </header>
  <p id="status">Loading…</p>
  <div id="root" class="shell" style="display:none"></div>
  <script>
  (function () {{
    // Theme sync
    function applyTheme(dark) {{ document.documentElement.classList.toggle("dark", dark); }}
    try {{
      const param = new URLSearchParams(location.search).get("theme");
      if (param !== null) {{ applyTheme(param === "dark"); }}
      else {{
        applyTheme(window.parent.document.documentElement.classList.contains("dark"));
        new MutationObserver(() =>
          applyTheme(window.parent.document.documentElement.classList.contains("dark"))
        ).observe(window.parent.document.documentElement, {{ attributes: true, attributeFilter: ["class"] }});
      }}
    }} catch (_) {{}}

    const status = document.getElementById("status");
    const root = document.getElementById("root");

    function esc(s) {{
      const d = document.createElement("div"); d.textContent = String(s ?? ""); return d.innerHTML;
    }}
    function clip(s, n) {{
      const t = (s || "").trim(); return t.length <= n ? t : t.slice(0, n - 1) + "…";
    }}

    function pillClass(verdict) {{
      return "pill pill-" + verdict;
    }}
    function pillLabel(verdict) {{
      return {{ remove: "Remove", uncertain: "Review", keep: "Keep" }}[verdict] || verdict;
    }}

    function scoreColor(score) {{
      if (score < 40) return "#b3261e";
      if (score < 65) return "#b06000";
      return "#137333";
    }}

    function signalStatusHtml(sig) {{
      if (sig.skipped) return "<span class='skip'>skip</span>";
      return sig.passed ? "<span class='pass'>✓ pass</span>" : "<span class='fail'>✗ fail</span>";
    }}

    function signalsTableHtml(signals) {{
      if (!signals || !signals.length) return "";
      let rows = "";
      for (const s of signals) {{
        const contrib = s.skipped ? "—" : (s.score_contribution + " / " + s.weight);
        rows += "<tr><td>" + esc(s.name) + "</td>"
          + "<td>" + signalStatusHtml(s) + "</td>"
          + "<td>" + esc(contrib) + "</td>"
          + "<td>" + esc(s.explanation) + "</td></tr>";
      }}
      return "<table class='signal-table'><thead>"
        + "<tr><th>Signal</th><th>Result</th><th>Score</th><th>Explanation</th></tr>"
        + "</thead><tbody>" + rows + "</tbody></table>";
    }}

    function savingsHtml(ts) {{
      if (!ts) return "";
      const p50 = (ts.p50_savings_seconds / 60).toFixed(1);
      const p5  = (ts.p5_savings_seconds / 60).toFixed(1);
      const p95 = (ts.p95_savings_seconds / 60).toFixed(1);
      const prob = (ts.prob_improvement * 100).toFixed(0);
      return "<div class='savings-box'>"
        + "<strong>⏱ Estimated time saving</strong><br>"
        + p50 + " min median &nbsp;·&nbsp; "
        + p5 + "–" + p95 + " min range &nbsp;·&nbsp; "
        + prob + "% probability of improvement"
        + " <span style='font-size:0.78rem;color:var(--muted)'>(" + esc(ts.n_simulations.toLocaleString()) + " Monte Carlo simulations)</span>"
        + "</div>";
    }}

    function edgeDetailHtml(edge) {{
      const score = edge.confidence_score;
      const scoreBar = "<div class='score-bar'><div class='score-fill' style='width:"
        + score + "%;background:" + scoreColor(score) + "'></div></div>";
      const fixHtml = edge.suggested_fix
        ? "<div class='suggestion-box'><strong>Suggestion:</strong> " + esc(edge.suggested_fix) + "</div>"
        : "";
      const diffHtml = edge.dag_diff
        ? "<details><summary>Suggested DAG change</summary><div class='diff-box'>" + esc(edge.dag_diff) + "</div></details>"
        : "";
      return scoreBar
        + "<p style='font-size:0.85rem;color:var(--muted);margin:0 0 0.75rem'>" + esc(edge.plain_explanation) + "</p>"
        + signalsTableHtml(edge.signals)
        + savingsHtml(edge.time_savings)
        + fixHtml
        + diffHtml;
    }}

    function redundantHtml(redundant) {{
      if (!redundant || !redundant.length) return "";
      let rows = redundant.map(r =>
        "<tr><td><code>" + esc(r.from_task) + " >> " + esc(r.to_task) + "</code></td>"
        + "<td>A longer path already covers this dependency — safe to remove.</td></tr>"
      ).join("");
      return "<details style='margin-bottom:0.75rem'>"
        + "<summary><strong>Redundant edges (" + redundant.length + ")</strong></summary>"
        + "<table class='signal-table'><thead><tr><th>Edge</th><th>Reason</th></tr></thead>"
        + "<tbody>" + rows + "</tbody></table></details>";
    }}

    fetch("recommendations.json", {{ credentials: "include" }})
      .then(r => r.ok ? r.json() : r.json().then(j => Promise.reject(j.error || r.statusText)))
      .then(data => {{
        status.textContent = "";
        root.style.display = "flex";

        const edges = data.edge_analyses || [];
        const gm = data.graph_metrics || {{}};
        const summ = data.summary || {{}};
        const redundant = data.redundant_edges || [];

        // Build list items from actionable edges (remove + uncertain first, then keep)
        const actionable = edges.filter(e => e.verdict === "remove" || e.verdict === "uncertain");
        const keeping = edges.filter(e => e.verdict === "keep");
        const items = [];

        const diagFooter = "<details style='margin-top:1rem'><summary>Analysis metadata</summary>"
          + "<div style='font-size:0.8rem;color:var(--muted);margin-top:0.5rem'>"
          + "<p>Policy: " + esc(data.policy) + " · schema " + esc(data.report_schema_version) + "</p>"
          + "<p>" + esc(gm.task_count) + " tasks · " + esc(gm.dependency_edge_count) + " edges · "
          + esc(gm.redundant_edge_count || 0) + " redundant</p>"
          + "<p>Historical data: " + (summ.has_historical_data
              ? esc(summ.profiled_task_count) + " tasks profiled" : "none") + "</p>"
          + "<p>Generated: " + esc(data.generated_at_utc) + " · provider " + esc(data.uape_provider_version) + "</p>"
          + "</div>" + redundantHtml(redundant) + "</details>";

        for (const edge of [...actionable, ...keeping]) {{
          const id = edge.from_task + ">>" + edge.to_task;
          const title = edge.from_task + " >> " + edge.to_task;
          items.push({{
            id,
            verdict: edge.verdict,
            title,
            preview: clip(edge.plain_explanation, 110),
            searchText: (title + " " + edge.plain_explanation + " "
              + (edge.signals || []).map(s => s.explanation).join(" ")).toLowerCase(),
            html: edgeDetailHtml(edge) + diagFooter,
          }});
        }}

        if (items.length === 0) {{
          items.push({{
            id: "__empty__", verdict: "keep",
            title: "No edges to display",
            preview: "This DAG has no declared dependencies to analyse.",
            searchText: "empty no edges",
            html: "<p>No declared edges found in this DAG.</p>" + diagFooter,
          }});
        }}

        // Counts
        const removeCount = summ.remove_count || 0;
        const uncertainCount = summ.uncertain_count || 0;
        const keepCount = summ.keep_count || 0;
        const countLabel = removeCount + " remove · " + uncertainCount + " review · " + keepCount + " keep";

        // Build panes
        const listPane = document.createElement("div");
        listPane.className = "list-pane";
        listPane.innerHTML = '<div class="list-head">'
          + '<h2>Edge analysis</h2>'
          + '<div class="count">' + esc(countLabel) + '</div>'
          + '<input type="search" id="q" class="search" placeholder="Filter edges…" '
          + 'autocomplete="off" aria-label="Filter" /></div>'
          + '<div class="list-scroll" id="listMount"></div>';

        // Capture element references from listPane's own tree — these work even before
        // listPane is appended to the document, and stay valid after appending.
        const searchInput = listPane.querySelector("#q");
        const listMount = listPane.querySelector("#listMount");

        const detailPane = document.createElement("div");
        detailPane.className = "detail-pane";
        const detailInner = document.createElement("div");
        detailInner.className = "detail-card";
        detailPane.appendChild(detailInner);

        let activeId = items[0].id;

        function renderList() {{
          const q = (searchInput.value || "").trim().toLowerCase();
          listMount.innerHTML = "";
          const filtered = q ? items.filter(it => it.searchText.includes(q)) : items;
          if (!filtered.length) {{
            listMount.innerHTML = "<div class='empty-list'>No items match your filter.</div>";
            return;
          }}
          for (const it of filtered) {{
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "list-item" + (it.id === activeId ? " active" : "");
            btn.dataset.id = it.id;
            const pill = document.createElement("span");
            pill.className = pillClass(it.verdict);
            pill.textContent = pillLabel(it.verdict);
            const chev = document.createElement("span");
            chev.className = "chev"; chev.textContent = "›";
            const row1 = document.createElement("div"); row1.className = "row1";
            row1.appendChild(pill); row1.appendChild(chev);
            const titleEl = document.createElement("div"); titleEl.className = "list-title";
            titleEl.textContent = it.title;
            const preview = document.createElement("div"); preview.className = "list-preview";
            preview.textContent = it.preview;
            btn.appendChild(row1); btn.appendChild(titleEl); btn.appendChild(preview);
            btn.addEventListener("click", () => {{ activeId = it.id; renderList(); showDetail(); }});
            listMount.appendChild(btn);
          }}
        }}

        function showDetail() {{
          const cur = items.find(x => x.id === activeId);
          if (!cur) return;
          const label = "UAPE · " + pillLabel(cur.verdict).toUpperCase();
          detailInner.innerHTML = "<p class='detail-meta'><strong>" + esc(label) + "</strong></p>"
            + "<h2>" + esc(cur.title) + "</h2>" + cur.html;
        }}

        searchInput.addEventListener("input", () => {{
          const q = (searchInput.value || "").trim().toLowerCase();
          const filtered = q ? items.filter(it => it.searchText.includes(q)) : items;
          if (filtered.length && !filtered.some(it => it.id === activeId)) {{
            activeId = filtered[0].id;
          }}
          renderList();
          if (filtered.length) showDetail();
        }});

        // Append panes first so renderList/showDetail can safely mutate the live DOM.
        root.appendChild(listPane);
        root.appendChild(detailPane);
        renderList();
        showDetail();
      }})
      .catch(e => {{
        status.innerHTML = '<p class="err">' + esc(String(e)) + "</p>";
      }});
  }})();
  </script>
</body>
</html>
"""


def create_uape_app() -> FastAPI:
    """FastAPI sub-application mounted under ``/uape`` on the API server."""
    app = FastAPI(
        title="UAPE advisory API",
        description=(
            "Uncertainty-Aware Parallelization Engine: per-edge dependency analysis "
            "with confidence scoring and Monte Carlo time-savings simulation."
        ),
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
    )

    @app.on_event("startup")
    def _startup() -> None:
        _ensure_cache_table()

    @app.get("/dags/{dag_id}/recommendations-ui", response_class=HTMLResponse)
    def recommendations_ui(dag_id: str) -> HTMLResponse:
        return HTMLResponse(_recommendations_ui_page(dag_id))

    @app.get("/dags/{dag_id}/recommendations.json")
    def recommendations_json(dag_id: str) -> JSONResponse:
        try:
            report = _load_report(dag_id)
        except AirflowException as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        return JSONResponse(report)

    return app
