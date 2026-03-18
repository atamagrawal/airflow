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
Data transformation operator that automatically infers and emits lineage.

Similar to SQLExecuteQueryOperator which parses SQL to extract lineage,
this operator infers lineage from the transformation configuration.
No explicit lineage input required!
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from airflow.providers.common.compat.openlineage.facet import (
    ColumnLineageDatasetFacet,
    Fields,
    InputDataset,
    InputField,
    OutputDataset,
)
from airflow.providers.common.compat.sdk import BaseOperator
from airflow.providers.openlineage.extractors.base import OperatorLineage

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


class DataTransformOperatorV2(BaseOperator):
    """
    Operator for data transformations that automatically infers and emits lineage.

    This operator performs data transformations based on configuration and
    automatically derives lineage from what it's doing - similar to how
    SQLExecuteQueryOperator parses SQL to extract lineage.

    The key difference: You tell it WHAT to do, and it figures out the lineage.

    :param source_table: Source table to read from (e.g., "public.orders")
    :param dest_table: Destination table to write to (e.g., "public.daily_summary")
    :param source_namespace: Source namespace (e.g., "postgres://localhost:5432/db")
    :param dest_namespace: Destination namespace (defaults to source_namespace)
    :param columns: List of column transformations. Each entry defines how to compute an output column.
        Format: [
            {"name": "order_id", "expr": "order_id"},  # Simple copy
            {"name": "total_amount", "expr": "SUM(amount)", "group_by": True},  # Aggregation
            {"name": "order_date", "expr": "DATE(created_at)"},  # Transform
        ]
    :param group_by_columns: Optional list of columns to group by (for aggregations)

    Example:
        >>> # Simple copy task
        >>> task = DataTransformOperatorV2(
        ...     task_id="copy_orders",
        ...     source_table="public.raw_orders",
        ...     dest_table="public.orders",
        ...     source_namespace="postgres://localhost:5432/prod",
        ...     columns=[
        ...         {"name": "order_id", "expr": "order_id"},
        ...         {"name": "amount", "expr": "ROUND(raw_amount, 2)"},
        ...     ]
        ... )
        >>>
        >>> # Aggregation task
        >>> task = DataTransformOperatorV2(
        ...     task_id="daily_summary",
        ...     source_table="public.orders",
        ...     dest_table="public.daily_summary",
        ...     source_namespace="postgres://localhost:5432/prod",
        ...     columns=[
        ...         {"name": "order_date", "expr": "order_date"},
        ...         {"name": "total_amount", "expr": "SUM(amount)"},
        ...         {"name": "order_count", "expr": "COUNT(order_id)"},
        ...     ],
        ...     group_by_columns=["order_date"]
        ... )
    """

    template_fields = ("source_table", "dest_table", "columns")
    ui_color = "#f0ede4"

    def __init__(
        self,
        *,
        source_table: str,
        dest_table: str,
        source_namespace: str,
        dest_namespace: str | None = None,
        columns: list[dict[str, str]],
        group_by_columns: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.source_table = source_table
        self.dest_table = dest_table
        self.source_namespace = source_namespace
        self.dest_namespace = dest_namespace or source_namespace
        self.columns = columns
        self.group_by_columns = group_by_columns or []

    def execute(self, context: Context) -> Any:
        """
        Execute the data transformation.

        In a real implementation, this would:
        1. Connect to source database
        2. Read source data
        3. Apply transformations based on column definitions
        4. Write to destination

        For this example, we simulate the transformation.
        """
        self.log.info("=" * 60)
        self.log.info("DATA TRANSFORMATION")
        self.log.info("=" * 60)
        self.log.info("Source: %s.%s", self.source_namespace, self.source_table)
        self.log.info("Destination: %s.%s", self.dest_namespace, self.dest_table)
        self.log.info("Transformations:")

        for col in self.columns:
            self.log.info("  - %s = %s", col["name"], col["expr"])

        if self.group_by_columns:
            self.log.info("Group by: %s", ", ".join(self.group_by_columns))

        self.log.info("=" * 60)

        # Simulate transformation work
        self.log.info("Reading from source table...")
        self.log.info("Applying %d column transformations...", len(self.columns))
        self.log.info("Writing to destination table...")

        self.log.info("✓ Transformation completed successfully")
        return {"rows_processed": 1000, "columns_transformed": len(self.columns)}

    def get_openlineage_facets_on_complete(self, task_instance) -> OperatorLineage:
        """
        Automatically infer and emit lineage from transformation configuration.

        This method analyzes the transformation config (columns, expressions)
        and automatically derives lineage - similar to how SQLExecuteQueryOperator
        parses SQL to extract lineage.

        No explicit lineage input needed - it's all inferred!
        """
        self.log.info("Inferring lineage from transformation configuration...")

        # Create input dataset
        inputs = [
            InputDataset(
                namespace=self.source_namespace,
                name=self.source_table,
            )
        ]

        # Infer column lineage by analyzing the transformation expressions
        column_lineage_fields = {}
        for col_config in self.columns:
            output_col = col_config["name"]
            expr = col_config["expr"]

            # Infer lineage from expression
            lineage_info = self._infer_column_lineage(output_col, expr)

            column_lineage_fields[output_col] = Fields(
                inputFields=[
                    InputField(
                        namespace=self.source_namespace,
                        name=self.source_table,
                        field=lineage_info["source_column"],
                    )
                ],
                transformationType=lineage_info["transformation_type"],
                transformationDescription=lineage_info["description"],
            )

        # Create output dataset with inferred column lineage
        facets = {}
        if column_lineage_fields:
            facets["columnLineage"] = ColumnLineageDatasetFacet(fields=column_lineage_fields)
            self.log.info(
                "Inferred lineage for %d columns automatically", len(column_lineage_fields)
            )

        outputs = [
            OutputDataset(
                namespace=self.dest_namespace,
                name=self.dest_table,
                facets=facets,
            )
        ]

        self.log.info(
            "Lineage emission: %d inputs → %d outputs (auto-inferred)",
            len(inputs),
            len(outputs),
        )

        return OperatorLineage(inputs=inputs, outputs=outputs)

    def _infer_column_lineage(self, output_col: str, expr: str) -> dict[str, str]:
        """
        Infer lineage information from transformation expression.

        This analyzes the expression to determine:
        - Source column(s)
        - Transformation type (IDENTITY, AGGREGATE, TRANSFORM)
        - Description

        This is similar to how SQL parsers work - analyzing the query
        to understand data flow.
        """
        expr_upper = expr.upper().strip()

        # Check for aggregation functions
        aggregate_functions = ["SUM", "COUNT", "AVG", "MAX", "MIN", "STDDEV"]
        for agg_func in aggregate_functions:
            if expr_upper.startswith(f"{agg_func}("):
                # Extract source column from aggregation
                source_col = expr[len(agg_func) + 1 : -1].strip()
                return {
                    "source_column": source_col,
                    "transformation_type": "AGGREGATE",
                    "description": f"{agg_func}({source_col})",
                }

        # Check for transformation functions
        transform_functions = ["DATE", "ROUND", "UPPER", "LOWER", "TRIM", "CAST", "CONCAT"]
        for trans_func in transform_functions:
            if expr_upper.startswith(f"{trans_func}("):
                # Extract source column from transformation
                # For simplicity, extract first argument
                inner = expr[len(trans_func) + 1 : -1].strip()
                source_col = inner.split(",")[0].strip()
                return {
                    "source_column": source_col,
                    "transformation_type": "TRANSFORM",
                    "description": f"{trans_func} transformation",
                }

        # Default: assume identity (direct copy)
        # The expression is the column name itself
        return {
            "source_column": expr,
            "transformation_type": "IDENTITY",
            "description": "Direct copy",
        }
