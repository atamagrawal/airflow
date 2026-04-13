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
    """Minimal HTML/JS for the Airflow plugin ``external_views`` iframe (dag tab)."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>UAPE — {html.escape(dag_id, quote=True)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1rem; line-height: 1.45; }}
    h1 {{ font-size: 1.1rem; }}
    .banner {{ background: #e8f4fc; border: 1px solid #b8d4e8; padding: 0.75rem; border-radius: 6px; margin-bottom: 1rem; }}
    .err {{ color: #b00020; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
    th, td {{ border: 1px solid #ccc; padding: 0.35rem 0.5rem; text-align: left; vertical-align: top; }}
    th {{ background: #f5f5f5; }}
    section {{ margin-top: 1.25rem; }}
    code {{ font-size: 0.9em; }}
  </style>
</head>
<body>
  <h1>UAPE parallelization advisory</h1>
  <p class="banner"><strong>Advisory only.</strong> Based on the serialized DAG graph only; hidden
  dependencies are out of scope. No task code is executed.</p>
  <p id="status">Loading…</p>
  <div id="root"></div>
  <script>
    const status = document.getElementById("status");
    const root = document.getElementById("root");
    function esc(s) {{
      const d = document.createElement("div");
      d.textContent = s;
      return d.innerHTML;
    }}
    fetch("recommendations.json", {{ credentials: "include" }})
      .then((r) => (r.ok ? r.json() : r.json().then((j) => Promise.reject(j.error || r.statusText))))
      .then((data) => {{
        status.textContent = "";
        let html = "<p><strong>Policy:</strong> " + esc(data.policy || "") + "</p>";
        html += "<p><strong>Clear allowlist:</strong> " + (data.clear_operator_allowlist || []).map(esc).join(", ") + "</p>";
        const hints = data.clear_task_overlap_hints || [];
        html += "<section><h2>Parallelization recommendations</h2>";
        if (hints.length === 0) {{
          html += "<p>No clear-task overlap hints (opaque tasks or no independent pairs).</p>";
        }} else {{
          html += "<table><thead><tr><th>Tasks</th><th>Proof</th><th>Caveat</th></tr></thead><tbody>";
          for (const h of hints) {{
            html += "<tr><td><code>" + esc(h.task_a) + "</code> ↔ <code>" + esc(h.task_b) + "</code></td>";
            html += "<td>" + esc((h.proof && h.proof.detail) || "") + "</td>";
            html += "<td>" + esc(h.caveat || "") + "</td></tr>";
          }}
          html += "</tbody></table>";
        }}
        html += "</section>";
        const abst = data.abstained_parallel_hints || [];
        if (abst.length > 0) {{
          html += "<section><h2>Abstentions</h2><table><thead><tr><th>Tasks</th><th>Reason</th></tr></thead><tbody>";
          for (const a of abst) {{
            html += "<tr><td><code>" + esc(a.task_a) + "</code> ↔ <code>" + esc(a.task_b) + "</code></td>";
            html += "<td>" + esc(a.abstain_reason || "") + "</td></tr>";
          }}
          html += "</tbody></table></section>";
        }}
        const cls = data.task_classifications || [];
        if (cls.length > 0) {{
          html += "<section><h2>Task classifications</h2><table><thead><tr><th>Task</th><th>Type</th><th>Opacity</th><th>Reason</th></tr></thead><tbody>";
          for (const t of cls) {{
            html += "<tr><td><code>" + esc(t.task_id) + "</code></td><td>" + esc(String(t.task_type || "—")) + "</td>";
            html += "<td>" + esc(t.opacity || "") + "</td><td>" + esc(t.opacity_reason || "") + "</td></tr>";
          }}
          html += "</tbody></table></section>";
        }}
        root.innerHTML = html;
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
        description="Read-only parallelization hints; JSON API and a small HTML page for the Airflow UI plugin tab.",
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
