#
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
"""
Comparison Engine for Shadow DAGs — AIP-09 §7.

After each shadow run completes the ``ComparisonEngine`` reads both the
production output sink and the shadow sink and produces a structured
``ComparisonReport``.  The ``verdict`` field drives alerting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from airflow.models.shadow_dag import ShadowDag
    from airflow.shadow.sink_proxy import ShadowContext

log = logging.getLogger(__name__)


class Verdict(str, Enum):
    """Outcome of a single shadow vs. production comparison run."""

    MATCH = "MATCH"
    WITHIN_THRESHOLD = "WITHIN_THRESHOLD"
    DIVERGED = "DIVERGED"
    SHADOW_FAILED = "SHADOW_FAILED"


@dataclass
class ColumnDiff:
    """Describes a schema difference between shadow and production outputs."""

    column: str
    change: str  # "added" | "removed" | "type_changed"
    prod_type: str | None = None
    shadow_type: str | None = None


@dataclass
class FieldStats:
    """Per-column statistical divergence between shadow and production."""

    column: str
    prod_null_rate: float = 0.0
    shadow_null_rate: float = 0.0
    prod_min: Any = None
    shadow_min: Any = None
    prod_max: Any = None
    shadow_max: Any = None
    prod_mean: float | None = None
    shadow_mean: float | None = None


@dataclass
class ComparisonReport:
    """
    Structured result of comparing one shadow run against its production
    counterpart (AIP-09 §7).

    Serialised as JSON and stored in ``ShadowDag.last_comparison_json`` after
    every shadow run for quick retrieval by the CLI and UI.
    """

    run_id: str
    shadow_id: str
    row_count_prod: int
    row_count_shadow: int
    row_count_delta_pct: float
    schema_divergence: list[ColumnDiff] = field(default_factory=list)
    value_divergence: list[FieldStats] = field(default_factory=list)
    sample_diff_rows: list[dict[str, Any]] = field(default_factory=list)
    verdict: Verdict = Verdict.MATCH
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "shadow_id": self.shadow_id,
            "row_count_prod": self.row_count_prod,
            "row_count_shadow": self.row_count_shadow,
            "row_count_delta_pct": self.row_count_delta_pct,
            "schema_divergence": [vars(d) for d in self.schema_divergence],
            "value_divergence": [vars(s) for s in self.value_divergence],
            "sample_diff_rows": self.sample_diff_rows,
            "verdict": self.verdict.value,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComparisonReport:
        return cls(
            run_id=data["run_id"],
            shadow_id=data["shadow_id"],
            row_count_prod=data.get("row_count_prod", 0),
            row_count_shadow=data.get("row_count_shadow", 0),
            row_count_delta_pct=data.get("row_count_delta_pct", 0.0),
            schema_divergence=[ColumnDiff(**d) for d in data.get("schema_divergence", [])],
            value_divergence=[FieldStats(**s) for s in data.get("value_divergence", [])],
            sample_diff_rows=data.get("sample_diff_rows", []),
            verdict=Verdict(data.get("verdict", Verdict.MATCH)),
            error=data.get("error"),
        )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON-lines file and return parsed rows.  Empty list on failure."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Shadow comparison: skipped malformed JSONL line in %s", path)
    return rows


def _column_names(rows: list[dict[str, Any]]) -> set[str]:
    if not rows:
        return set()
    return set(rows[0].keys())


def _detect_schema_divergence(
    prod_rows: list[dict[str, Any]],
    shadow_rows: list[dict[str, Any]],
) -> list[ColumnDiff]:
    prod_cols = _column_names(prod_rows)
    shadow_cols = _column_names(shadow_rows)
    diffs: list[ColumnDiff] = []
    for col in prod_cols - shadow_cols:
        diffs.append(ColumnDiff(column=col, change="removed"))
    for col in shadow_cols - prod_cols:
        diffs.append(ColumnDiff(column=col, change="added"))
    return diffs


def _detect_value_divergence(
    prod_rows: list[dict[str, Any]],
    shadow_rows: list[dict[str, Any]],
    common_cols: set[str],
) -> list[FieldStats]:
    stats: list[FieldStats] = []
    for col in sorted(common_cols):
        prod_vals = [r[col] for r in prod_rows if col in r]
        shadow_vals = [r[col] for r in shadow_rows if col in r]
        prod_nulls = sum(1 for v in prod_vals if v is None) / max(len(prod_vals), 1)
        shadow_nulls = sum(1 for v in shadow_vals if v is None) / max(len(shadow_vals), 1)
        numeric_prod = [v for v in prod_vals if isinstance(v, (int, float))]
        numeric_shadow = [v for v in shadow_vals if isinstance(v, (int, float))]
        stats.append(
            FieldStats(
                column=col,
                prod_null_rate=prod_nulls,
                shadow_null_rate=shadow_nulls,
                prod_min=min(numeric_prod, default=None),
                shadow_min=min(numeric_shadow, default=None),
                prod_max=max(numeric_prod, default=None),
                shadow_max=max(numeric_shadow, default=None),
                prod_mean=(sum(numeric_prod) / len(numeric_prod)) if numeric_prod else None,
                shadow_mean=(sum(numeric_shadow) / len(numeric_shadow)) if numeric_shadow else None,
            )
        )
    return stats


def _determine_verdict(
    delta_pct: float,
    schema_diffs: list[ColumnDiff],
    alert_threshold: float,
) -> Verdict:
    if schema_diffs:
        return Verdict.DIVERGED
    if abs(delta_pct) > alert_threshold * 100:
        return Verdict.DIVERGED
    if abs(delta_pct) > 0:
        return Verdict.WITHIN_THRESHOLD
    return Verdict.MATCH


class ComparisonEngine:
    """
    Compares shadow vs. production sink outputs after each shadow run.

    For the local ``LocalFileSinkProxy`` the engine reads JSON-lines files from
    the shadow sink root.  The production outputs are expected at the path
    stored in ``DagRun.conf["__prod_sink_root__"]`` (also a JSON-lines dir for
    local mode).

    Cloud sink comparisons (BigQuery, GCS) are handled by sub-classes that
    override ``_read_prod_rows`` and ``_read_shadow_rows``.
    """

    def compare(
        self,
        shadow_ctx: ShadowContext,
        shadow_dag: ShadowDag,
        prod_sink_root: Path | None = None,
    ) -> ComparisonReport:
        """
        Compare one shadow run against the production run.

        :param shadow_ctx: Runtime context for the shadow run.
        :param shadow_dag: The ``ShadowDag`` metadata record.
        :param prod_sink_root: Optional path to production output for local mode.
        :returns: A ``ComparisonReport`` with verdict and diff details.
        """
        try:
            shadow_rows = self._read_shadow_rows(shadow_ctx)
            prod_rows = self._read_prod_rows(prod_sink_root, shadow_ctx)
        except Exception as exc:
            log.exception("Shadow comparison failed to read output data for %s", shadow_ctx.shadow_id)
            return ComparisonReport(
                run_id=shadow_ctx.run_id,
                shadow_id=shadow_ctx.shadow_id,
                row_count_prod=0,
                row_count_shadow=0,
                row_count_delta_pct=0.0,
                verdict=Verdict.SHADOW_FAILED,
                error=str(exc),
            )

        n_prod = len(prod_rows)
        n_shadow = len(shadow_rows)
        if n_prod > 0:
            delta_pct = ((n_shadow - n_prod) / n_prod) * 100.0
        else:
            delta_pct = 0.0 if n_shadow == 0 else float("inf")

        schema_diffs = _detect_schema_divergence(prod_rows, shadow_rows)
        common_cols = _column_names(prod_rows) & _column_names(shadow_rows)
        value_diffs = _detect_value_divergence(prod_rows, shadow_rows, common_cols)

        # Collect up to 100 differing rows for the report
        sample_diffs: list[dict[str, Any]] = []
        for i, (p, s) in enumerate(zip(prod_rows, shadow_rows)):
            if i >= 100:
                break
            if p != s:
                sample_diffs.append({"prod": p, "shadow": s})

        verdict = _determine_verdict(delta_pct, schema_diffs, shadow_dag.divergence_alert_pct)

        return ComparisonReport(
            run_id=shadow_ctx.run_id,
            shadow_id=shadow_ctx.shadow_id,
            row_count_prod=n_prod,
            row_count_shadow=n_shadow,
            row_count_delta_pct=round(delta_pct, 4),
            schema_divergence=schema_diffs,
            value_divergence=value_diffs,
            sample_diff_rows=sample_diffs,
            verdict=verdict,
        )

    def _read_shadow_rows(self, shadow_ctx: ShadowContext) -> list[dict[str, Any]]:
        """Read all rows emitted by the shadow run under ``sink_root``."""
        rows: list[dict[str, Any]] = []
        if shadow_ctx.sink_root.exists():
            for output_file in sorted(shadow_ctx.sink_root.rglob("output.jsonl")):
                rows.extend(_load_jsonl(output_file))
        return rows

    def _read_prod_rows(
        self,
        prod_sink_root: Path | None,
        shadow_ctx: ShadowContext,
    ) -> list[dict[str, Any]]:
        """Read production output rows.  Falls back to empty list in local mode."""
        if prod_sink_root is None:
            return []
        rows: list[dict[str, Any]] = []
        if prod_sink_root.exists():
            for output_file in sorted(prod_sink_root.rglob("output.jsonl")):
                rows.extend(_load_jsonl(output_file))
        return rows
