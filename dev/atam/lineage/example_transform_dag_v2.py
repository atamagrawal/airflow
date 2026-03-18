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
Example DAG showing DataTransformOperatorV2.

The key difference: You specify WHAT transformation to do,
and lineage is automatically inferred - not explicitly provided!

Similar to SQLExecuteQueryOperator:
- SQLExecuteQueryOperator: You provide SQL, it parses to extract lineage
- DataTransformOperatorV2: You provide transformation config, it infers lineage
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Add path
sys.path.insert(0, str(Path(__file__).parent))

from airflow import DAG
from transform_operator_v2 import DataTransformOperatorV2

with DAG(
    dag_id="example_transform_v2",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "transform", "v2"],
    doc_md=__doc__,
) as dag:
    # Example 1: Simple Copy (IDENTITY transformation)
    # Just specify the columns to copy - lineage is inferred automatically!
    copy_orders = DataTransformOperatorV2(
        task_id="copy_orders",
        source_table="public.raw_orders",
        dest_table="public.orders",
        source_namespace="postgres://prod-db:5432/production",
        columns=[
            # Simple column copies - operator infers IDENTITY transformation
            {"name": "order_id", "expr": "order_id"},
            {"name": "customer_id", "expr": "customer_id"},
            {"name": "amount", "expr": "amount"},
            {"name": "order_date", "expr": "order_date"},
        ],
        # Lineage automatically inferred:
        # - order_id ← raw_orders.order_id (IDENTITY)
        # - customer_id ← raw_orders.customer_id (IDENTITY)
        # - amount ← raw_orders.amount (IDENTITY)
        # - order_date ← raw_orders.order_date (IDENTITY)
    )

    # Example 2: Data Cleaning (TRANSFORM)
    # Specify transformations - operator infers transformation type!
    clean_orders = DataTransformOperatorV2(
        task_id="clean_orders",
        source_table="public.orders",
        dest_table="staging.clean_orders",
        source_namespace="postgres://prod-db:5432/production",
        columns=[
            {"name": "order_id", "expr": "order_id"},
            # Transformations - operator automatically infers TRANSFORM type
            {"name": "amount", "expr": "ROUND(amount, 2)"},  # Round to 2 decimals
            {"name": "order_date", "expr": "DATE(order_date)"},  # Extract date
            {"name": "customer_id", "expr": "UPPER(customer_id)"},  # Uppercase
        ],
        # Lineage automatically inferred:
        # - order_id ← orders.order_id (IDENTITY)
        # - amount ← orders.amount (TRANSFORM: ROUND)
        # - order_date ← orders.order_date (TRANSFORM: DATE)
        # - customer_id ← orders.customer_id (TRANSFORM: UPPER)
    )

    # Example 3: Aggregation (AGGREGATE)
    # Specify aggregations - operator infers AGGREGATE transformation!
    daily_summary = DataTransformOperatorV2(
        task_id="daily_summary",
        source_table="staging.clean_orders",
        dest_table="public.daily_summary",
        source_namespace="postgres://prod-db:5432/production",
        columns=[
            {"name": "order_date", "expr": "order_date"},
            # Aggregations - operator automatically infers AGGREGATE type
            {"name": "total_orders", "expr": "COUNT(order_id)"},
            {"name": "total_amount", "expr": "SUM(amount)"},
            {"name": "avg_amount", "expr": "AVG(amount)"},
            {"name": "max_amount", "expr": "MAX(amount)"},
            {"name": "min_amount", "expr": "MIN(amount)"},
        ],
        group_by_columns=["order_date"],
        # Lineage automatically inferred:
        # - order_date ← clean_orders.order_date (IDENTITY)
        # - total_orders ← clean_orders.order_id (AGGREGATE: COUNT)
        # - total_amount ← clean_orders.amount (AGGREGATE: SUM)
        # - avg_amount ← clean_orders.amount (AGGREGATE: AVG)
        # - max_amount ← clean_orders.amount (AGGREGATE: MAX)
        # - min_amount ← clean_orders.amount (AGGREGATE: MIN)
    )

    # Example 4: Mixed transformations
    # Combination of identity, transform, and aggregate
    customer_metrics = DataTransformOperatorV2(
        task_id="customer_metrics",
        source_table="staging.clean_orders",
        dest_table="public.customer_metrics",
        source_namespace="postgres://prod-db:5432/production",
        columns=[
            {"name": "customer_id", "expr": "customer_id"},  # IDENTITY
            {"name": "total_spent", "expr": "SUM(amount)"},  # AGGREGATE
            {"name": "order_count", "expr": "COUNT(order_id)"},  # AGGREGATE
            {"name": "avg_order_value", "expr": "AVG(amount)"},  # AGGREGATE
            {"name": "first_order_date", "expr": "MIN(order_date)"},  # AGGREGATE
            {"name": "last_order_date", "expr": "MAX(order_date)"},  # AGGREGATE
        ],
        group_by_columns=["customer_id"],
        # All lineage automatically inferred from expressions!
    )

    # Example 5: Cross-database transformation
    # Same transformation logic, different namespaces
    sync_to_analytics = DataTransformOperatorV2(
        task_id="sync_to_analytics",
        source_table="public.daily_summary",
        dest_table="analytics.daily_summary",
        source_namespace="postgres://prod-db:5432/production",
        dest_namespace="postgres://analytics-db:5432/analytics",  # Different database!
        columns=[
            {"name": "order_date", "expr": "order_date"},
            {"name": "total_orders", "expr": "total_orders"},
            {"name": "total_amount", "expr": "total_amount"},
            {"name": "avg_amount", "expr": "avg_amount"},
        ],
        # Lineage tracks cross-database flow automatically!
    )

    # Set up pipeline
    # Notice: No explicit lineage configuration anywhere!
    # Everything is inferred from transformation specifications
    copy_orders >> clean_orders >> daily_summary >> sync_to_analytics
    clean_orders >> customer_metrics
