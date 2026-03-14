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

from json_validator_operator import JsonValidatorOperator


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
# DAG 1: Complete Validation Pipeline (Single Record)
# ============================================================================

with DAG(
    dag_id="json_validation_complete_pipeline",
    description="Complete data validation pipeline with JsonValidatorOperator",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "json", "validation", "pipeline"],
    doc_md="""
    # JSON Validation Pipeline

    This DAG demonstrates a complete data validation workflow:

    1. **Fetch Data** - Simulate API call to get user data
    2. **Validate Required** - Ensure critical fields are present
    3. **Validate Schema** - Check data types and structure
    4. **Extract Fields** - Keep only needed fields
    5. **Clean Data** - Remove null values and transform
    6. **Process** - Use the validated data

    ## Testing

    Trigger manually:
    ```
    airflow dags trigger json_validation_complete_pipeline
    ```
    """,
) as complete_pipeline_dag:

    # Step 1: Fetch data from API
    fetch_user_data = PythonOperator(
        task_id="fetch_user_data",
        python_callable=simulate_api_call,
        doc_md="Simulate fetching user data from an external API",
    )

    # Step 2: Validate required fields are present
    validate_required_fields = JsonValidatorOperator(
        task_id="validate_required_fields",
        json_data="{{ ti.xcom_pull(task_ids='fetch_user_data') }}",
        required_keys=["user_id", "email", "username"],
        doc_md="Ensure critical fields are present in the API response",
    )

    # Step 3: Validate data against schema
    validate_user_schema = JsonValidatorOperator(
        task_id="validate_user_schema",
        json_data="{{ ti.xcom_pull(task_ids='fetch_user_data') }}",
        schema={
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "username": {"type": "string", "minLength": 1},
                "email": {"type": "string", "format": "email"},
                "age": {"type": "integer", "minimum": 0, "maximum": 150},
                "is_active": {"type": "boolean"},
                "roles": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["user_id", "username", "email"],
        },
        doc_md="Validate data types and structure against JSON schema",
    )

    # Step 4: Extract only needed fields
    extract_user_fields = JsonValidatorOperator(
        task_id="extract_user_fields",
        json_data="{{ ti.xcom_pull(task_ids='fetch_user_data') }}",
        extract_keys=["user_id", "username", "email", "is_active", "created_at"],
        doc_md="Extract only the fields needed for downstream processing",
    )

    # Step 5: Clean the data (remove null values)
    clean_user_data = JsonValidatorOperator(
        task_id="clean_user_data",
        json_data="{{ ti.xcom_pull(task_ids='extract_user_fields') }}",
        transform_func="lambda x: {k: v for k, v in x.items() if v is not None}",
        doc_md="Remove null values from the extracted data",
    )

    # Step 6: Process the validated and cleaned data
    process_user_data = PythonOperator(
        task_id="process_user_data",
        python_callable=process_validated_data,
        doc_md="Process the validated user data",
    )

    # Define task dependencies
    (
        fetch_user_data
        >> [validate_required_fields, validate_user_schema]
        >> extract_user_fields
        >> clean_user_data
        >> process_user_data
    )


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


# ============================================================================
# DAG 3: Validation with Graceful Failures
# ============================================================================

with DAG(
    dag_id="json_validation_graceful_failures",
    description="Handle validation failures gracefully",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "json", "validation", "error-handling"],
) as graceful_failures_dag:

    def fetch_potentially_invalid_data(**context):
        """Simulate fetching data that might be incomplete."""
        # This data is missing the 'email' field
        return {
            "user_id": 99999,
            "username": "incomplete_user",
            # email is missing
            "status": "pending",
        }

    def handle_validation_success(**context):
        """Execute if validation passes."""
        print("Data validation successful - proceeding with processing")
        return "success"

    def handle_validation_skip(**context):
        """Execute if validation was skipped."""
        print("Data validation failed - performing cleanup or alerting")
        return "skipped"

    fetch_data = PythonOperator(
        task_id="fetch_potentially_invalid_data",
        python_callable=fetch_potentially_invalid_data,
    )

    # This will skip instead of fail if email is missing
    validate_optional = JsonValidatorOperator(
        task_id="validate_optional_email",
        json_data="{{ ti.xcom_pull(task_ids='fetch_potentially_invalid_data') }}",
        required_keys=["user_id", "username", "email"],
        fail_on_invalid=False,  # Skip instead of fail
    )

    # These tasks run regardless of validation result
    success_handler = PythonOperator(
        task_id="handle_success",
        python_callable=handle_validation_success,
        trigger_rule="none_failed",  # Runs if upstream didn't fail
    )

    skip_handler = PythonOperator(
        task_id="handle_skip",
        python_callable=handle_validation_skip,
        trigger_rule="one_failed",  # Runs if upstream failed/skipped
    )

    fetch_data >> validate_optional >> [success_handler, skip_handler]


# ============================================================================
# DAG 4: Dynamic Validation from DAG Config
# ============================================================================

with DAG(
    dag_id="json_validation_dynamic_config",
    description="Dynamic validation based on DAG configuration",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "json", "validation", "dynamic"],
    doc_md="""
    # Dynamic Validation

    Trigger with custom configuration:

    ```bash
    airflow dags trigger json_validation_dynamic_config \\
      --conf '{"required_fields": ["id", "name"], "min_age": 18}'
    ```
    """,
) as dynamic_dag:

    def generate_test_data(**context):
        """Generate test data based on DAG config."""
        conf = context.get("dag_run").conf or {}
        return {
            "id": conf.get("id", 123),
            "name": conf.get("name", "Test User"),
            "age": conf.get("age", 25),
            "email": "test@example.com",
        }

    generate_data = PythonOperator(
        task_id="generate_test_data",
        python_callable=generate_test_data,
    )

    # Use DAG config to determine required fields
    validate_dynamic = JsonValidatorOperator(
        task_id="validate_with_dynamic_requirements",
        json_data="{{ ti.xcom_pull(task_ids='generate_test_data') }}",
        required_keys="{{ dag_run.conf.get('required_fields', ['id', 'name', 'email']) }}",
    )

    generate_data >> validate_dynamic


# ============================================================================
# DAG 5: Simple Examples
# ============================================================================

with DAG(
    dag_id="json_validation_simple_examples",
    description="Simple standalone validation examples",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "json", "validation", "simple"],
) as simple_examples_dag:

    # Example 1: Validate static JSON
    validate_static = JsonValidatorOperator(
        task_id="validate_static_json",
        json_data={"id": 1, "name": "Static Data", "status": "active"},
        required_keys=["id", "name"],
    )

    # Example 2: Extract fields from static JSON
    extract_from_static = JsonValidatorOperator(
        task_id="extract_fields_static",
        json_data={
            "id": 100,
            "name": "Product",
            "price": 29.99,
            "description": "A great product",
            "internal_code": "XYZ-123",
        },
        extract_keys=["id", "name", "price"],  # Remove internal fields
    )

    # Example 3: Transform data
    transform_data = JsonValidatorOperator(
        task_id="transform_uppercase",
        json_data={"name": "john doe", "city": "new york"},
        transform_func="lambda x: {k: v.upper() if isinstance(v, str) else v for k, v in x.items()}",
    )

    # Run examples in parallel
    [validate_static, extract_from_static, transform_data]
