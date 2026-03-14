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
Example tutorial operator demonstrating key Airflow operator patterns.

This operator filters data based on a threshold value and demonstrates:
- Proper operator structure
- Template fields for Jinja templating
- Type hints and documentation
- Context usage and XCom
- Logging patterns
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from airflow.providers.standard.version_compat import BaseOperator

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context


class DataFilterOperator(BaseOperator):
    """
    Filter a list of numeric values based on a threshold.

    This operator demonstrates common patterns for data processing operators:
    - Takes input data and processes it
    - Uses a threshold parameter for filtering
    - Returns filtered results via XCom
    - Supports Jinja templating for dynamic values

    .. seealso::
        This is a tutorial example. For production use, consider using
        PythonOperator or custom operators specific to your data source.

    :param data: List of numeric values to filter. Can be templated.
    :param threshold: Numeric threshold value. Values >= threshold are kept.
        Can be templated.
    :param operator: Comparison operator ('gte', 'lte', 'eq', 'gt', 'lt').
        Default is 'gte' (greater than or equal).
    :param return_count_only: If True, return only the count of filtered items
        instead of the full list. Default is False.

    **Example usage:**

    .. code-block:: python

        from airflow import DAG
        from datetime import datetime
        from dev.sample_data_filter_operator import DataFilterOperator

        with DAG(
            "data_filter_example",
            start_date=datetime(2024, 1, 1),
            schedule=None,
        ) as dag:
            # Filter values >= 50
            filter_task = DataFilterOperator(
                task_id="filter_data",
                data=[10, 25, 50, 75, 100],
                threshold=50,
                operator="gte",
            )

            # Use templating to get threshold from DAG config
            dynamic_filter = DataFilterOperator(
                task_id="dynamic_filter",
                data="{{ dag_run.conf.get('data', [1, 2, 3]) }}",
                threshold="{{ dag_run.conf.get('threshold', 2) }}",
            )

    **XCom output:**

    The filtered data can be accessed by downstream tasks:

    .. code-block:: python

        def process_filtered_data(**context):
            filtered = context["ti"].xcom_pull(task_ids="filter_data")
            print(f"Filtered data: {filtered}")

    """

    # Define which fields support Jinja templating
    # These fields will be rendered with the DAG context before execute() runs
    template_fields: Sequence[str] = ("data", "threshold")

    # UI color for task in the graph view
    ui_color = "#e8f7f0"
    ui_fgcolor = "#000"

    def __init__(
        self,
        *,
        data: list[float | int] | str,
        threshold: float | int | str,
        operator: str = "gte",
        return_count_only: bool = False,
        **kwargs,
    ) -> None:
        """
        Initialize the DataFilterOperator.

        Note: All operator-specific parameters must be keyword-only (after *).
        This is an Airflow convention for clarity.
        """
        super().__init__(**kwargs)
        self.data = data
        self.threshold = threshold
        self.operator = operator
        self.return_count_only = return_count_only

        # Validate operator parameter at initialization
        valid_operators = {"gte", "lte", "eq", "gt", "lt"}
        if self.operator not in valid_operators:
            raise ValueError(
                f"Invalid operator '{self.operator}'. "
                f"Must be one of: {', '.join(valid_operators)}"
            )

    def execute(self, context: Context) -> list[float | int] | int:
        """
        Execute the operator logic.

        This is the main method that runs when the task executes.
        It receives the full Airflow context with task instance, dag run, etc.

        :param context: Airflow context dict containing task instance, dag_run,
            execution_date, and other context variables
        :return: Filtered data list or count of filtered items
        """
        # Log important information for debugging
        self.log.info("Starting data filtering operation")
        self.log.info("Input data type: %s", type(self.data))
        self.log.info("Threshold: %s (type: %s)", self.threshold, type(self.threshold))

        # Convert threshold to numeric if it's a string (from templating)
        threshold_value = self._convert_to_numeric(self.threshold, "threshold")

        # Convert data to list of numerics if it's a string (from templating)
        if isinstance(self.data, str):
            import json

            try:
                data_list = json.loads(self.data)
            except json.JSONDecodeError:
                # Try splitting by comma if JSON parsing fails
                data_list = [item.strip() for item in self.data.split(",")]
        else:
            data_list = self.data

        # Convert all data items to numeric
        numeric_data = []
        for i, item in enumerate(data_list):
            try:
                numeric_data.append(self._convert_to_numeric(item, f"data[{i}]"))
            except (ValueError, TypeError) as e:
                self.log.warning("Skipping invalid data item at index %d: %s", i, e)

        self.log.info("Processing %d numeric values", len(numeric_data))

        # Filter data based on operator
        filtered_data = self._apply_filter(numeric_data, threshold_value)

        self.log.info(
            "Filtered %d values from %d total (kept %d%%)",
            len(numeric_data) - len(filtered_data),
            len(numeric_data),
            int(len(filtered_data) / len(numeric_data) * 100) if numeric_data else 0,
        )

        # Return count or full data based on configuration
        if self.return_count_only:
            result = len(filtered_data)
            self.log.info("Returning count: %d", result)
        else:
            result = filtered_data
            self.log.info("Returning filtered data: %s", result)

        # The return value is automatically pushed to XCom if do_xcom_push is True
        return result

    def _convert_to_numeric(self, value: Any, field_name: str) -> float | int:
        """Convert a value to numeric type."""
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            try:
                # Try int first, then float
                if "." in value:
                    return float(value)
                return int(value)
            except ValueError:
                raise ValueError(f"{field_name} must be numeric, got: {value!r}")
        raise TypeError(f"{field_name} must be numeric, got type: {type(value)}")

    def _apply_filter(
        self, data: list[float | int], threshold: float | int
    ) -> list[float | int]:
        """Apply the filtering operation based on the configured operator."""
        if self.operator == "gte":
            return [x for x in data if x >= threshold]
        elif self.operator == "lte":
            return [x for x in data if x <= threshold]
        elif self.operator == "eq":
            return [x for x in data if x == threshold]
        elif self.operator == "gt":
            return [x for x in data if x > threshold]
        elif self.operator == "lt":
            return [x for x in data if x < threshold]
        else:
            # This should never happen due to validation in __init__
            raise ValueError(f"Invalid operator: {self.operator}")


# ============================================================================
# Additional Example: Operator with Hook Integration
# ============================================================================


class DataAggregationOperator(BaseOperator):
    """
    Example operator that demonstrates aggregation patterns.

    This shows additional patterns:
    - Multiple aggregation operations
    - More complex return types
    - Documentation of return structure

    :param data: List of numeric values to aggregate
    :param operations: List of operations to perform ('sum', 'mean', 'min', 'max', 'count')

    **Example:**

    .. code-block:: python

        aggregate = DataAggregationOperator(
            task_id="aggregate_data",
            data=[10, 20, 30, 40, 50],
            operations=["sum", "mean", "max"],
        )
    """

    template_fields: Sequence[str] = ("data",)
    ui_color = "#fff4e6"

    def __init__(
        self,
        *,
        data: list[float | int] | str,
        operations: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.data = data
        self.operations = operations or ["sum", "mean"]

        valid_ops = {"sum", "mean", "min", "max", "count", "median"}
        invalid_ops = set(self.operations) - valid_ops
        if invalid_ops:
            raise ValueError(f"Invalid operations: {invalid_ops}")

    def execute(self, context: Context) -> dict[str, float | int]:
        """
        Aggregate data and return results as a dictionary.

        :return: Dictionary mapping operation names to computed values
            Example: {"sum": 150, "mean": 30.0, "max": 50}
        """
        # Parse data (simplified for example)
        if isinstance(self.data, str):
            import json

            data_list = json.loads(self.data)
        else:
            data_list = self.data

        results = {}

        if not data_list:
            self.log.warning("Empty data list provided")
            return {op: 0 for op in self.operations}

        for operation in self.operations:
            if operation == "sum":
                results[operation] = sum(data_list)
            elif operation == "mean":
                results[operation] = sum(data_list) / len(data_list)
            elif operation == "min":
                results[operation] = min(data_list)
            elif operation == "max":
                results[operation] = max(data_list)
            elif operation == "count":
                results[operation] = len(data_list)
            elif operation == "median":
                sorted_data = sorted(data_list)
                n = len(sorted_data)
                if n % 2 == 0:
                    results[operation] = (sorted_data[n // 2 - 1] + sorted_data[n // 2]) / 2
                else:
                    results[operation] = sorted_data[n // 2]

            self.log.info("%s: %s", operation, results[operation])

        return results
