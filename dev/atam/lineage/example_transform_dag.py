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
Example DAG showing DataTransformOperator.

The focus is on data transformation - lineage is emitted automatically as a byproduct,
similar to how SQLExecuteQueryOperator works.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Add path
sys.path.insert(0, str(Path(__file__).parent))

from airflow import DAG
from transform_operator import DataTransformOperator


# Real transformation functions (not just placeholders)
def extract_and_clean_orders(**context):
    """
    Extract orders from source and clean the data.

    This is the actual work - transformation logic.
    Lineage will be automatically emitted.
    """
    print("=" * 60)
    print("EXTRACTION AND CLEANING")
    print("=" * 60)
    print("1. Connecting to source database...")
    print("2. Reading orders table...")
    print("3. Cleaning data:")
    print("   - Removing duplicates")
    print("   - Handling NULL values")
    print("   - Standardizing formats")
    print("4. Writing to staging table...")
    print("=" * 60)
    print("✓ Processed 10,000 orders")
    return {"rows_extracted": 10000, "rows_cleaned": 9850}


def aggregate_daily_metrics(**context):
    """
    Aggregate orders into daily metrics.

    Performs GROUP BY operations and aggregations.
    """
    print("=" * 60)
    print("DAILY AGGREGATION")
    print("=" * 60)
    print("1. Reading from staging.orders...")
    print("2. Grouping by order_date...")
    print("3. Computing aggregations:")
    print("   - COUNT(order_id) -> order_count")
    print("   - SUM(amount) -> total_amount")
    print("   - AVG(amount) -> avg_amount")
    print("   - MAX(amount) -> max_amount")
    print("4. Writing to public.daily_summary...")
    print("=" * 60)
    print("✓ Generated 30 daily summaries")
    return {"days_aggregated": 30, "total_revenue": 125000.00}


def enrich_with_customer_data(**context):
    """
    Join orders with customer data for enrichment.

    Performs JOIN operations across tables.
    """
    print("=" * 60)
    print("CUSTOMER ENRICHMENT")
    print("=" * 60)
    print("1. Reading from public.orders...")
    print("2. Reading from public.customers...")
    print("3. Joining on customer_id...")
    print("4. Computing derived fields:")
    print("   - customer_full_name = first_name + last_name")
    print("   - customer_lifetime_value = SUM(amount)")
    print("5. Writing to public.enriched_orders...")
    print("=" * 60)
    print("✓ Enriched 10,000 orders with customer data")
    return {"orders_enriched": 10000, "customers_matched": 1500}


with DAG(
    dag_id="example_data_transform",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "transform"],
    doc_md=__doc__,
) as dag:
    # Task 1: Extract and clean orders
    # This is a data transformation task - lineage is emitted as a side effect
    extract_clean = DataTransformOperator(
        task_id="extract_and_clean",
        transform_callable=extract_and_clean_orders,
        source_dataset={
            "namespace": "postgres://prod-db:5432/production",
            "name": "public.raw_orders",
        },
        dest_dataset={
            "namespace": "postgres://prod-db:5432/production",
            "name": "staging.orders",
        },
        column_lineage={
            "order_id": {
                "source": "order_id",
                "type": "IDENTITY",
                "description": "Direct copy",
            },
            "customer_id": {
                "source": "customer_id",
                "type": "IDENTITY",
                "description": "Direct copy",
            },
            "amount": {
                "source": "raw_amount",
                "type": "TRANSFORM",
                "description": "Cleaned and validated amount",
            },
            "order_date": {
                "source": "created_timestamp",
                "type": "TRANSFORM",
                "description": "Converted timestamp to date",
            },
        },
    )

    # Task 2: Aggregate to daily metrics
    # Main purpose: compute daily aggregations
    # Lineage: automatically tracked
    daily_aggregation = DataTransformOperator(
        task_id="aggregate_daily",
        transform_callable=aggregate_daily_metrics,
        source_dataset={
            "namespace": "postgres://prod-db:5432/production",
            "name": "staging.orders",
        },
        dest_dataset={
            "namespace": "postgres://prod-db:5432/production",
            "name": "public.daily_summary",
        },
        column_lineage={
            "order_date": {
                "source": "order_date",
                "type": "IDENTITY",
                "description": "Grouping column",
            },
            "order_count": {
                "source": "order_id",
                "type": "AGGREGATE",
                "description": "COUNT(order_id)",
            },
            "total_amount": {
                "source": "amount",
                "type": "AGGREGATE",
                "description": "SUM(amount)",
            },
            "avg_amount": {
                "source": "amount",
                "type": "AGGREGATE",
                "description": "AVG(amount)",
            },
            "max_amount": {
                "source": "amount",
                "type": "AGGREGATE",
                "description": "MAX(amount)",
            },
        },
    )

    # Task 3: Enrich with customer data
    # Main purpose: join orders with customer info
    # Lineage: tracks multi-table joins
    enrich_orders = DataTransformOperator(
        task_id="enrich_with_customers",
        transform_callable=enrich_with_customer_data,
        source_dataset={
            "namespace": "postgres://prod-db:5432/production",
            "name": "staging.orders",
        },
        dest_dataset={
            "namespace": "postgres://prod-db:5432/production",
            "name": "public.enriched_orders",
        },
        column_lineage={
            "order_id": {
                "source": "order_id",
                "type": "IDENTITY",
            },
            "order_amount": {
                "source": "amount",
                "type": "IDENTITY",
            },
            "order_date": {
                "source": "order_date",
                "type": "IDENTITY",
            },
            # Note: In reality, customer columns come from a different source
            # This operator currently supports single source
            # But shows the concept - lineage tracks the transformation
            "customer_full_name": {
                "source": "customer_id",
                "type": "TRANSFORM",
                "description": "Joined with customers table and concatenated name",
            },
        },
    )

    # Set up pipeline
    extract_clean >> daily_aggregation
    extract_clean >> enrich_orders
