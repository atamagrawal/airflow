# JsonValidatorOperator

A production-ready Airflow operator for validating and transforming JSON data in your data pipelines.

## Overview

The `JsonValidatorOperator` validates JSON data against schemas, checks for required keys, extracts specific fields, and applies transformations. It's particularly useful for:

- 🔍 **API Response Validation** - Ensure external APIs return expected data
- ✅ **Data Quality Checks** - Validate data contracts between pipeline stages
- 🎯 **Field Extraction** - Pull specific fields from complex JSON structures
- 🔄 **Data Transformation** - Clean and transform JSON data inline

## Installation

The operator is self-contained and only requires Airflow. For schema validation support, install:

```bash
pip install jsonschema
```

## Quick Start

### Basic Validation

```python
from airflow import DAG
from datetime import datetime
from json_validator_operator import JsonValidatorOperator

with DAG("validate_example", start_date=datetime(2024, 1, 1)) as dag:
    validate_data = JsonValidatorOperator(
        task_id="validate_api_response",
        json_data="{{ ti.xcom_pull(task_ids='fetch_api') }}",
        required_keys=["id", "name", "email"],
    )
```

### Schema Validation

```python
validate_schema = JsonValidatorOperator(
    task_id="validate_user_schema",
    json_data="{{ ti.xcom_pull(task_ids='get_user') }}",
    schema={
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "email": {"type": "string", "format": "email"},
            "age": {"type": "integer", "minimum": 0}
        },
        "required": ["id", "name", "email"]
    },
)
```

### Extract Specific Fields

```python
extract_user_info = JsonValidatorOperator(
    task_id="extract_fields",
    json_data="{{ ti.xcom_pull(task_ids='get_full_user_data') }}",
    extract_keys=["user_id", "email", "created_at"],
)
```

### Transform Data

```python
clean_data = JsonValidatorOperator(
    task_id="remove_nulls",
    json_data="{{ ti.xcom_pull(task_ids='raw_data') }}",
    transform_func="lambda x: {k: v for k, v in x.items() if v is not None}",
)
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `json_data` | str \| dict | **Required** | JSON data to validate (templated) |
| `schema` | dict | None | JSON schema for validation (templated) |
| `required_keys` | list[str] | [] | Keys that must be present |
| `extract_keys` | list[str] | None | Keys to extract from data |
| `fail_on_invalid` | bool | True | Fail task on validation error |
| `transform_func` | str | None | Python lambda to transform data |

## Features

### 1. Required Keys Validation

Ensure critical fields are present in your data:

```python
validate = JsonValidatorOperator(
    task_id="check_required",
    json_data='{"id": 1, "name": "John"}',
    required_keys=["id", "name", "email"],  # Will fail - email missing
)
```

**Output:**
```
AirflowException: Missing required keys: ['email']
```

### 2. JSON Schema Validation

Validate complex data structures with JSON Schema (requires `jsonschema` library):

```python
validate_product = JsonValidatorOperator(
    task_id="validate_product",
    json_data="{{ ti.xcom_pull(task_ids='get_product') }}",
    schema={
        "type": "object",
        "properties": {
            "product_id": {"type": "integer"},
            "name": {"type": "string", "minLength": 1},
            "price": {"type": "number", "minimum": 0},
            "in_stock": {"type": "boolean"},
            "tags": {
                "type": "array",
                "items": {"type": "string"}
            }
        },
        "required": ["product_id", "name", "price"]
    },
)
```

### 3. Field Extraction

Extract only the fields you need:

```python
# Input: {"id": 1, "name": "John", "email": "john@example.com", "age": 30, "address": {...}}
# Output: {"id": 1, "email": "john@example.com"}

extract = JsonValidatorOperator(
    task_id="extract_user_id_email",
    json_data="{{ ti.xcom_pull(task_ids='get_user') }}",
    extract_keys=["id", "email"],
)
```

Works with lists too:

```python
# Input: [{"id": 1, "name": "John"}, {"id": 2, "name": "Jane"}]
# Output: [{"id": 1}, {"id": 2}]

extract_ids = JsonValidatorOperator(
    task_id="extract_all_ids",
    json_data="{{ ti.xcom_pull(task_ids='get_users') }}",
    extract_keys=["id"],
)
```

### 4. Data Transformation

Apply Python transformations inline:

```python
# Remove null values
clean = JsonValidatorOperator(
    task_id="remove_nulls",
    json_data='{"a": 1, "b": null, "c": 3}',
    transform_func="lambda x: {k: v for k, v in x.items() if v is not None}",
)
# Result: {"a": 1, "c": 3}

# Convert strings to uppercase
uppercase = JsonValidatorOperator(
    task_id="uppercase_name",
    json_data='{"name": "john"}',
    transform_func="lambda x: {**x, 'name': x['name'].upper()}",
)
# Result: {"name": "JOHN"}
```

### 5. Graceful Failures

Control task behavior on validation errors:

```python
# Fail the task (default)
validate_strict = JsonValidatorOperator(
    task_id="strict_validation",
    json_data="{{ ti.xcom_pull(task_ids='data') }}",
    required_keys=["id"],
    fail_on_invalid=True,  # Raises AirflowException
)

# Skip the task
validate_lenient = JsonValidatorOperator(
    task_id="lenient_validation",
    json_data="{{ ti.xcom_pull(task_ids='data') }}",
    required_keys=["id"],
    fail_on_invalid=False,  # Raises AirflowSkipException
)
```

## Complete Example DAG

```python
from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from datetime import datetime
import json

# Add dev directory to path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from json_validator_operator import JsonValidatorOperator


def fetch_api_data(**context):
    """Simulate API call."""
    return {
        "user_id": 12345,
        "name": "John Doe",
        "email": "john@example.com",
        "age": 30,
        "address": {
            "street": "123 Main St",
            "city": "San Francisco",
            "zip": "94102"
        },
        "preferences": {
            "theme": "dark",
            "notifications": True
        },
        "metadata": None
    }


def process_validated_data(**context):
    """Process the validated and extracted data."""
    ti = context["ti"]
    data = ti.xcom_pull(task_ids="extract_user_info")
    print(f"Processing user: {data}")
    return f"Processed user {data.get('user_id')}"


with DAG(
    dag_id="json_validation_pipeline",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "json", "validation"],
) as dag:

    # Step 1: Fetch data from API
    fetch_data = PythonOperator(
        task_id="fetch_api_data",
        python_callable=fetch_api_data,
    )

    # Step 2: Validate required fields
    validate_required = JsonValidatorOperator(
        task_id="validate_required_fields",
        json_data="{{ ti.xcom_pull(task_ids='fetch_api_data') }}",
        required_keys=["user_id", "name", "email"],
    )

    # Step 3: Validate with schema
    validate_schema = JsonValidatorOperator(
        task_id="validate_schema",
        json_data="{{ ti.xcom_pull(task_ids='fetch_api_data') }}",
        schema={
            "type": "object",
            "properties": {
                "user_id": {"type": "integer"},
                "name": {"type": "string", "minLength": 1},
                "email": {"type": "string", "format": "email"},
                "age": {"type": "integer", "minimum": 0, "maximum": 150}
            },
            "required": ["user_id", "name", "email"]
        },
    )

    # Step 4: Extract only needed fields
    extract_info = JsonValidatorOperator(
        task_id="extract_user_info",
        json_data="{{ ti.xcom_pull(task_ids='fetch_api_data') }}",
        extract_keys=["user_id", "name", "email"],
    )

    # Step 5: Clean the data (remove null values)
    clean_data = JsonValidatorOperator(
        task_id="clean_data",
        json_data="{{ ti.xcom_pull(task_ids='extract_user_info') }}",
        transform_func="lambda x: {k: v for k, v in x.items() if v is not None}",
    )

    # Step 6: Process the cleaned data
    process_data = PythonOperator(
        task_id="process_data",
        python_callable=process_validated_data,
    )

    # Define dependencies
    fetch_data >> [validate_required, validate_schema] >> extract_info >> clean_data >> process_data
```

## Error Handling

### Missing Required Keys

```python
# This will fail with: AirflowException: Missing required keys: ['email']
validate = JsonValidatorOperator(
    task_id="validate",
    json_data='{"id": 1, "name": "John"}',
    required_keys=["id", "name", "email"],
)
```

### Invalid JSON

```python
# This will fail with: AirflowException: Invalid JSON data
validate = JsonValidatorOperator(
    task_id="validate",
    json_data='{"invalid": json}',  # Malformed JSON
    required_keys=["id"],
)
```

### Schema Validation Failure

```python
# This will fail with: AirflowException: Schema validation failed: 'john' is not of type 'integer'
validate = JsonValidatorOperator(
    task_id="validate",
    json_data='{"id": "john"}',
    schema={
        "type": "object",
        "properties": {"id": {"type": "integer"}}
    },
)
```

## Testing

Example test cases:

```python
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from json_validator_operator import JsonValidatorOperator


def test_required_keys_present():
    """Test validation passes when all required keys are present."""
    operator = JsonValidatorOperator(
        task_id="test",
        json_data={"id": 1, "name": "John", "email": "john@example.com"},
        required_keys=["id", "name", "email"],
    )
    result = operator.execute({})
    assert result["id"] == 1
    assert result["name"] == "John"


def test_missing_required_keys_fails():
    """Test validation fails when required keys are missing."""
    from airflow.providers.common.compat.sdk import AirflowException

    operator = JsonValidatorOperator(
        task_id="test",
        json_data={"id": 1, "name": "John"},
        required_keys=["id", "name", "email"],
    )
    with pytest.raises(AirflowException, match="Missing required keys"):
        operator.execute({})


def test_extract_keys():
    """Test extracting specific keys."""
    operator = JsonValidatorOperator(
        task_id="test",
        json_data={"id": 1, "name": "John", "age": 30, "city": "SF"},
        extract_keys=["id", "name"],
    )
    result = operator.execute({})
    assert result == {"id": 1, "name": "John"}
    assert "age" not in result


def test_transform_function():
    """Test data transformation."""
    operator = JsonValidatorOperator(
        task_id="test",
        json_data={"a": 1, "b": None, "c": 3},
        transform_func="lambda x: {k: v for k, v in x.items() if v is not None}",
    )
    result = operator.execute({})
    assert result == {"a": 1, "c": 3}
```

Run tests:

```bash
uv run --project airflow-core pytest dev/test_json_validator.py -xvs
```

## Best Practices

### 1. Use Template Fields

Always use Jinja templates to pull data from upstream tasks:

```python
# Good
json_data="{{ ti.xcom_pull(task_ids='upstream') }}"

# Avoid
json_data=some_static_value
```

### 2. Validate Early

Place validation operators immediately after data ingestion:

```python
fetch_api >> validate_data >> process_data
```

### 3. Combine Validations

Use multiple validation operators for different concerns:

```python
fetch >> validate_required >> validate_schema >> extract_fields >> process
```

### 4. Handle Failures Gracefully

Use `fail_on_invalid=False` for optional validations:

```python
validate_optional = JsonValidatorOperator(
    task_id="validate_optional_fields",
    json_data="{{ ti.xcom_pull(task_ids='data') }}",
    required_keys=["optional_field"],
    fail_on_invalid=False,  # Skip if missing
)
```

### 5. Keep Transformations Simple

Complex transformations should use PythonOperator:

```python
# Simple - OK for JsonValidatorOperator
transform_func="lambda x: {k: v for k, v in x.items() if v}"

# Complex - Use PythonOperator instead
def complex_transform(**context):
    data = context["ti"].xcom_pull(task_ids="upstream")
    # Multiple lines of transformation logic
    return transformed_data
```

## Operator Design Patterns

This operator demonstrates several key Airflow patterns:

### Pattern 1: Template Fields
```python
template_fields: Sequence[str] = ("json_data", "schema")
```
Allows dynamic values from DAG context.

### Pattern 2: Type Hints
```python
def execute(self, context: Context) -> dict[str, Any] | list[Any]:
```
Provides IDE support and documentation.

### Pattern 3: Logging
```python
self.log.info("Starting JSON validation")
self.log.warning("Key '%s' not found in data", key)
```
Uses Airflow's logger, not print statements.

### Pattern 4: Error Handling
```python
if self.fail_on_invalid:
    raise AirflowException(error_msg)
else:
    raise AirflowSkipException(error_msg)
```
Provides control over failure behavior.

### Pattern 5: Flexible Input
```python
def _parse_json(self, json_data: str | dict[str, Any]) -> dict[str, Any]:
```
Handles both string and dict inputs.

## Performance Considerations

- **Schema Validation**: Most expensive operation. Use sparingly for critical validations.
- **Transformations**: Keep simple. Complex transformations should use PythonOperator.
- **Large JSON**: For very large JSON (>10MB), consider using a PythonOperator with streaming.

## Troubleshooting

### Issue: "jsonschema library not available"

**Solution:** Install jsonschema library:
```bash
pip install jsonschema
```

### Issue: "Cannot validate required keys on list data"

**Solution:** The data is a list, but required_keys expects a dict. Either:
- Validate list items individually in a loop
- Check if data structure matches expectations

### Issue: Transform function fails

**Solution:** Ensure lambda is properly quoted and escaped:
```python
# Good
transform_func="lambda x: {k: v for k, v in x.items()}"

# Bad (syntax error)
transform_func="lambda x: {k: v for k, v in x.items()"  # Missing closing brace
```

## Related Operators

- **PythonOperator** - For complex transformations
- **BranchPythonOperator** - For conditional logic based on data
- **ShortCircuitOperator** - To stop pipeline on validation failure

## Contributing

To extend this operator:

1. Add new validation methods (e.g., `_validate_data_types`)
2. Support more schema formats (e.g., Avro, Protobuf)
3. Add caching for repeated validations
4. Support async validation for external APIs

## License

Apache License 2.0 - Same as Apache Airflow

## Additional Resources

- [JSON Schema Documentation](https://json-schema.org/)
- [Airflow Custom Operators Guide](https://airflow.apache.org/docs/apache-airflow/stable/howto/custom-operator.html)
- [Airflow XCom Documentation](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/xcoms.html)
