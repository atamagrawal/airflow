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
Example DAG demonstrating the use of custom data processing operators.

This DAG shows:
- How to use custom operators
- Task dependencies and XCom data passing
- Using PythonOperator to process results from custom operators
- DAG-level configuration with dag_run.conf
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator

# Add dev directory to path so we can import the sample operator
sys.path.insert(0, str(Path(__file__).parent))

# Import our custom operators
from sample_data_filter_operator import DataAggregationOperator, DataFilterOperator


def generate_sample_data(**context):
    """Generate sample data for processing."""
    import random

    data = [random.randint(1, 100) for _ in range(20)]
    print(f"Generated {len(data)} data points: {data}")
    return data


def process_filtered_results(**context):
    """Process results from the filter operator."""
    ti = context["ti"]

    # Pull data from upstream tasks using XCom
    filtered_data = ti.xcom_pull(task_ids="filter_high_values")
    filtered_count = ti.xcom_pull(task_ids="count_low_values")
    aggregation_results = ti.xcom_pull(task_ids="aggregate_data")

    print("=" * 60)
    print("Processing Results:")
    print("=" * 60)
    print(f"High values (>= 50): {filtered_data}")
    print(f"Count of low values (< 50): {filtered_count}")
    print(f"Aggregation results: {aggregation_results}")
    print("=" * 60)

    return {
        "high_values_count": len(filtered_data) if filtered_data else 0,
        "low_values_count": filtered_count,
        "summary": aggregation_results,
    }


# Define the DAG
with DAG(
    dag_id="example_data_filter_operators",
    description="Example DAG using custom data processing operators",
    start_date=datetime(2024, 1, 1),
    schedule=None,  # Manual trigger only
    catchup=False,
    tags=["example", "tutorial", "custom-operator"],
    # You can pass configuration when triggering the DAG:
    # airflow dags trigger example_data_filter_operators --conf '{"threshold": 60}'
) as dag:
    # Task 1: Generate sample data
    generate_data = PythonOperator(
        task_id="generate_data",
        python_callable=generate_sample_data,
    )

    # Task 2: Filter for high values (>= 50)
    filter_high = DataFilterOperator(
        task_id="filter_high_values",
        data="{{ ti.xcom_pull(task_ids='generate_data') }}",
        threshold="{{ dag_run.conf.get('threshold', 50) }}",  # Allow dynamic threshold
        operator="gte",
        return_count_only=False,  # Return full list
    )

    # Task 3: Count low values (< 50)
    count_low = DataFilterOperator(
        task_id="count_low_values",
        data="{{ ti.xcom_pull(task_ids='generate_data') }}",
        threshold=50,
        operator="lt",
        return_count_only=True,  # Return only count
    )

    # Task 4: Aggregate the original data
    aggregate = DataAggregationOperator(
        task_id="aggregate_data",
        data="{{ ti.xcom_pull(task_ids='generate_data') }}",
        operations=["sum", "mean", "min", "max", "count"],
    )

    # Task 5: Process and display results
    process_results = PythonOperator(
        task_id="process_results",
        python_callable=process_filtered_results,
    )

    # Define task dependencies
    # generate_data runs first, then the three processing tasks run in parallel,
    # then process_results runs last
    generate_data >> [filter_high, count_low, aggregate] >> process_results


# ============================================================================
# Example: Simpler DAG with static data
# ============================================================================

with DAG(
    dag_id="example_simple_data_filter",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "tutorial", "simple"],
) as simple_dag:
    # Use static data directly
    filter_task = DataFilterOperator(
        task_id="filter_static_data",
        data=[10, 25, 30, 50, 75, 100, 125],
        threshold=50,
        operator="gte",
    )

    def print_results(**context):
        ti = context["ti"]
        result = ti.xcom_pull(task_ids="filter_static_data")
        print(f"Filtered values >= 50: {result}")

    print_task = PythonOperator(
        task_id="print_filtered_results",
        python_callable=print_results,
    )

    filter_task >> print_task
