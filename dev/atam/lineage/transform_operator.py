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
Data transformation operator that emits lineage as a byproduct.

Similar to SQLExecuteQueryOperator which executes SQL and emits lineage,
this operator performs data transformations and emits lineage metadata.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

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


class DataTransformOperator(BaseOperator):
    """
    Operator for data transformations that automatically emits lineage.

    This operator is designed to perform data transformations (similar to how
    SQLExecuteQueryOperator executes SQL). As a byproduct, it emits OpenLineage
    metadata about table and column lineage.

    The primary purpose is data transformation - lineage is automatically tracked.

    :param transform_callable: A Python callable that performs the data transformation.
        The callable should accept context and return any value.
    :param source_dataset: Source dataset information as dict with 'namespace' and 'name'.
        Example: {"namespace": "postgres://localhost:5432/db", "name": "public.orders"}
    :param dest_dataset: Destination dataset information.
    :param column_lineage: Optional dict mapping output columns to their source columns and transformations.
        Example: {"total_amount": {"source": "amount", "type": "AGGREGATE", "description": "SUM"}}
    :param op_args: Positional arguments for transform_callable
    :param op_kwargs: Keyword arguments for transform_callable

    Example:
        >>> def aggregate_orders(**context):
        ...     # Actual transformation logic
        ...     print("Reading from orders table...")
        ...     print("Aggregating by date...")
        ...     print("Writing to daily_summary...")
        ...     return {"rows_processed": 1000}
        >>>
        >>> task = DataTransformOperator(
        ...     task_id="aggregate_daily_orders",
        ...     transform_callable=aggregate_orders,
        ...     source_dataset={
        ...         "namespace": "postgres://localhost:5432/prod",
        ...         "name": "public.orders"
        ...     },
        ...     dest_dataset={
        ...         "namespace": "postgres://localhost:5432/prod",
        ...         "name": "public.daily_summary"
        ...     },
        ...     column_lineage={
        ...         "order_date": {"source": "order_date", "type": "IDENTITY"},
        ...         "total_amount": {"source": "amount", "type": "AGGREGATE", "description": "SUM"},
        ...         "order_count": {"source": "order_id", "type": "AGGREGATE", "description": "COUNT"},
        ...     }
        ... )
    """

    template_fields = ("op_args", "op_kwargs", "source_dataset", "dest_dataset")
    template_fields_renderers = {"op_args": "py", "op_kwargs": "py"}
    ui_color = "#f0ede4"

    def __init__(
        self,
        *,
        transform_callable: Callable,
        source_dataset: dict[str, str] | None = None,
        dest_dataset: dict[str, str] | None = None,
        column_lineage: dict[str, dict[str, str]] | None = None,
        op_args: tuple | None = None,
        op_kwargs: dict | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.transform_callable = transform_callable
        self.source_dataset = source_dataset
        self.dest_dataset = dest_dataset
        self.column_lineage = column_lineage or {}
        self.op_args = op_args or ()
        self.op_kwargs = op_kwargs or {}

    def execute(self, context: Context) -> Any:
        """
        Execute the data transformation.

        This is the main operation - performing the actual data transformation.
        Lineage will be emitted automatically after completion.
        """
        self.log.info("Starting data transformation")
        if self.source_dataset and self.dest_dataset:
            self.log.info(
                "Transforming: %s -> %s",
                self.source_dataset.get("name"),
                self.dest_dataset.get("name"),
            )

        # Execute the actual transformation
        result = self.transform_callable(*self.op_args, **self.op_kwargs)

        self.log.info("Data transformation completed successfully")
        return result

    def get_openlineage_facets_on_complete(self, task_instance) -> OperatorLineage:
        """
        Emit OpenLineage metadata after transformation completes.

        This is called automatically by Airflow's OpenLineage integration.
        It emits lineage as a byproduct of the transformation operation,
        similar to how SQLExecuteQueryOperator emits lineage from SQL parsing.
        """
        # If no datasets configured, return empty lineage
        if not self.source_dataset or not self.dest_dataset:
            self.log.debug("No dataset configuration found, skipping lineage emission")
            return OperatorLineage()

        self.log.info("Emitting lineage metadata for transformation")

        # Create input dataset
        inputs = [
            InputDataset(
                namespace=self.source_dataset["namespace"],
                name=self.source_dataset["name"],
            )
        ]

        # Build column lineage facets if configured
        facets = {}
        if self.column_lineage:
            column_lineage_fields = {}
            for output_col, lineage_info in self.column_lineage.items():
                source_col = lineage_info.get("source", output_col)
                transformation_type = lineage_info.get("type", "IDENTITY")
                transformation_desc = lineage_info.get("description")

                column_lineage_fields[output_col] = Fields(
                    inputFields=[
                        InputField(
                            namespace=self.source_dataset["namespace"],
                            name=self.source_dataset["name"],
                            field=source_col,
                        )
                    ],
                    transformationType=transformation_type,
                    transformationDescription=transformation_desc,
                )

            facets["columnLineage"] = ColumnLineageDatasetFacet(fields=column_lineage_fields)
            self.log.info("Column lineage configured for %d columns", len(column_lineage_fields))

        # Create output dataset with facets
        outputs = [
            OutputDataset(
                namespace=self.dest_dataset["namespace"],
                name=self.dest_dataset["name"],
                facets=facets,
            )
        ]

        return OperatorLineage(inputs=inputs, outputs=outputs)
