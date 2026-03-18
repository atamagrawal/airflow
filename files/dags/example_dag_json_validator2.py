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
Example DAG demonstrating JsonValidatorOperator usage.

This DAG shows a realistic data validation pipeline:
1. Fetch data from an API (simulated)
2. Validate required fields are present
3. Validate data against a schema
4. Extract only needed fields
5. Clean and transform the data
6. Process the validated data
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator

# Add dev directory to path
sys.path.insert(0, str(Path(__file__).parent))

from dev.atam.custom.json_validator_operator import JsonValidatorOperator

# ============================================================================
# Helper Functions
# ============================================================================


def simulate_api_call(**context):
    """
    Simulate fetching data from an external API.

    In production, this would be an actual API call using
    requests, HttpOperator, or a provider-specific operator.
    """
    return {
        "user_id": 12345,
        "username": "john_doe",
        "email": "john@example.com",
        "first_name": "John",
        "last_name": "Doe",
        "age": 30,
        "is_active": True,
        "created_at": "2024-01-15T10:30:00Z",
        "updated_at": "2024-03-01T15:45:00Z",
        "address": {
            "street": "123 Main St",
            "city": "San Francisco",
            "state": "CA",
            "zip": "94102",
            "country": "USA",
        },
        "preferences": {
            "theme": "dark",
            "notifications_enabled": True,
            "language": "en",
        },
        "roles": ["user", "editor"],
        "metadata": {
            "source": "api",
            "version": "v2",
            "internal_notes": None,  # This will be cleaned
        },
    }


def simulate_batch_api_call(**context):
    """Simulate fetching multiple records from an API."""
    return [
        {
            "user_id": 1,
            "username": "alice",
            "email": "alice@example.com",
            "status": "active",
            "score": 95,
        },
        {
            "user_id": 2,
            "username": "bob",
            "email": "bob@example.com",
            "status": "active",
            "score": 87,
        },
        {
            "user_id": 3,
            "username": "charlie",
            "email": "charlie@example.com",
            "status": "inactive",
            "score": 62,
        },
    ]


def process_validated_data(**context):
    """Process the validated and cleaned data."""
    ti = context["ti"]
    user_data = ti.xcom_pull(task_ids="clean_user_data")

    print("=" * 80)
    print("Processing Validated User Data")
    print("=" * 80)
    print(f"User ID: {user_data.get('user_id')}")
    print(f"Email: {user_data.get('email')}")
    print(f"Active: {user_data.get('is_active')}")
    print("=" * 80)

    return {"status": "success", "user_id": user_data.get("user_id")}


def process_batch_data(**context):
    """Process batch validated data."""
    ti = context["ti"]
    users = ti.xcom_pull(task_ids="extract_batch_fields")

    print("=" * 80)
    print(f"Processing {len(users)} validated users")
    print("=" * 80)
    for user in users:
        print(f"  - {user['username']} ({user['email']}): {user['status']}")
    print("=" * 80)

    return {"total_processed": len(users)}

# ============================================================================
# DAG 2: Batch Validation (Multiple Records)
# ============================================================================

with DAG(
    dag_id="json_validation_batch_processing",
    description="Validate and process multiple records",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "json", "validation", "batch"],
) as batch_dag:

    # Fetch batch of records
    fetch_batch = PythonOperator(
        task_id="fetch_batch_data",
        python_callable=simulate_batch_api_call,
    )

    # Extract specific fields from all records
    extract_batch_fields = JsonValidatorOperator(
        task_id="extract_batch_fields",
        json_data="{{ ti.xcom_pull(task_ids='fetch_batch_data') }}",
        extract_keys=["user_id", "username", "email", "status"],
    )

    # Process the batch
    process_batch = PythonOperator(
        task_id="process_batch",
        python_callable=process_batch_data,
    )

    fetch_batch >> extract_batch_fields >> process_batch

