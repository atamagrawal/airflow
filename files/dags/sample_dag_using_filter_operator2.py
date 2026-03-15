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
from dev.sample_data_filter_operator import DataAggregationOperator, DataFilterOperator

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


if __name__ == "__main__":
    from dag_test_helper import dag_run    
    dag_run(simple_dag)
