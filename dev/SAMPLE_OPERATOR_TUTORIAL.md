# Sample Operator Tutorial

This tutorial demonstrates how to create custom Airflow operators with proper patterns and testing.

## Files Created

1. **`sample_data_filter_operator.py`** - Two example operators:
   - `DataFilterOperator` - Filters numeric data based on a threshold
   - `DataAggregationOperator` - Aggregates data with multiple operations

2. **`sample_dag_using_filter_operator.py`** - Example DAGs showing:
   - How to use custom operators
   - Task dependencies
   - XCom data passing
   - Templating with Jinja2

3. **`test_sample_data_filter_operator.py`** - Comprehensive test suite demonstrating:
   - Pytest patterns
   - Parametrized tests
   - Testing edge cases
   - Error condition testing

## Key Operator Patterns Demonstrated

### 1. Operator Structure

```python
class MyOperator(BaseOperator):
    """Docstring with description and params."""

    # Fields that support Jinja templating
    template_fields: Sequence[str] = ("field1", "field2")

    # UI customization
    ui_color = "#e8f7f0"

    def __init__(self, *, param1, param2, **kwargs):
        """Initialize with keyword-only parameters."""
        super().__init__(**kwargs)
        self.param1 = param1
        self.param2 = param2

    def execute(self, context: Context):
        """Main execution logic."""
        # Your logic here
        return result  # Automatically pushed to XCom
```

### 2. Template Fields

Template fields allow dynamic values using Jinja2 syntax:

```python
# In operator definition
template_fields: Sequence[str] = ("data", "threshold")

# In DAG
filter_task = DataFilterOperator(
    task_id="filter",
    data="{{ ti.xcom_pull(task_ids='upstream_task') }}",
    threshold="{{ dag_run.conf.get('threshold', 50) }}",
)
```

### 3. Context Usage

The `execute` method receives the full Airflow context:

```python
def execute(self, context: Context):
    # Access task instance
    ti = context["ti"]

    # Access execution date
    execution_date = context["execution_date"]

    # Pull XCom data
    upstream_data = ti.xcom_pull(task_ids="upstream_task")

    # Logging
    self.log.info("Processing data")
```

### 4. XCom (Cross-Communication)

Return values are automatically pushed to XCom:

```python
def execute(self, context: Context):
    result = [1, 2, 3]
    return result  # Available to downstream tasks
```

Downstream tasks can pull this data:

```python
def process_results(**context):
    ti = context["ti"]
    data = ti.xcom_pull(task_ids="upstream_task")
    print(f"Received: {data}")
```

### 5. Type Hints

Use proper type hints for better IDE support:

```python
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from airflow.providers.common.compat.sdk import Context

class MyOperator(BaseOperator):
    template_fields: Sequence[str] = ("field",)

    def execute(self, context: Context) -> list[int]:
        return [1, 2, 3]
```

### 6. Logging

Use `self.log` for logging (not `print`):

```python
self.log.info("Processing %d items", count)
self.log.warning("Skipping invalid item: %s", item)
self.log.error("Failed to process: %s", error)
```

### 7. Error Handling

```python
from airflow.providers.common.compat.sdk import AirflowException, AirflowSkipException

def execute(self, context: Context):
    if not self.data:
        # Skip this task
        raise AirflowSkipException("No data to process")

    if invalid_config:
        # Fail the task
        raise AirflowException("Invalid configuration")
```

## Running the Examples

### 1. Run the Tests

```bash
# Run all tests for the operator
uv run --project airflow-core pytest dev/test_sample_data_filter_operator.py -xvs

# Run specific test
uv run --project airflow-core pytest dev/test_sample_data_filter_operator.py::TestDataFilterOperator::test_basic_gte_filter -xvs

# Run with parametrized tests
uv run --project airflow-core pytest dev/test_sample_data_filter_operator.py::TestDataFilterOperator::test_all_operators -xvs
```

### 2. Test the DAG

First, copy the DAG to your dags folder or test it in Breeze:

```bash
# In Breeze environment
breeze run airflow dags test example_data_filter_operators

# Or test with specific execution date
breeze run airflow dags test example_data_filter_operators 2024-01-01
```

### 3. Trigger the DAG with Config

```bash
# Trigger with custom threshold
breeze run airflow dags trigger example_data_filter_operators \
  --conf '{"threshold": 60}'
```

## Testing Best Practices

### 1. Use Pytest Patterns

```python
class TestMyOperator:
    def test_basic_case(self):
        """Test description."""
        operator = MyOperator(task_id="test", param=value)
        result = operator.execute({})
        assert result == expected
```

### 2. Parametrized Tests

```python
@pytest.mark.parametrize(
    "input_val,expected",
    [
        (10, 20),
        (20, 40),
        (30, 60),
    ],
)
def test_multiple_cases(self, input_val, expected):
    operator = MyOperator(task_id="test", value=input_val)
    result = operator.execute({})
    assert result == expected
```

### 3. Test Error Conditions

```python
def test_invalid_input_raises_error(self):
    with pytest.raises(ValueError, match="Invalid input"):
        MyOperator(task_id="test", invalid_param="bad")
```

### 4. Test Edge Cases

- Empty inputs
- Single item
- Large datasets
- None values
- Type conversions

## Operator Development Checklist

When creating a new operator:

- [ ] Inherit from `BaseOperator`
- [ ] Add comprehensive docstring with params and examples
- [ ] Define `template_fields` for Jinja templating support
- [ ] Use keyword-only parameters in `__init__` (after `*`)
- [ ] Call `super().__init__(**kwargs)`
- [ ] Implement `execute(self, context: Context)` method
- [ ] Add proper type hints
- [ ] Use `self.log` for logging (not `print`)
- [ ] Validate parameters in `__init__`
- [ ] Return meaningful values for XCom
- [ ] Add UI customization (`ui_color`, etc.)
- [ ] Write comprehensive tests
- [ ] Test with various input types (especially strings from templating)
- [ ] Handle edge cases (empty data, None values)
- [ ] Add proper error messages

## Common Patterns

### Pattern 1: Processing Data from Upstream Tasks

```python
filter_task = DataFilterOperator(
    task_id="filter",
    data="{{ ti.xcom_pull(task_ids='generate_data') }}",
    threshold=50,
)
```

### Pattern 2: Using DAG Run Configuration

```python
dynamic_task = DataFilterOperator(
    task_id="dynamic",
    data=[1, 2, 3],
    threshold="{{ dag_run.conf.get('threshold', 10) }}",
)
```

### Pattern 3: Chaining Multiple Operators

```python
generate >> [filter_high, filter_low, aggregate] >> process
```

### Pattern 4: Conditional Returns

```python
def execute(self, context: Context):
    result = self._process_data()
    if self.return_summary:
        return {"count": len(result), "max": max(result)}
    return result
```

## Architecture Boundaries to Remember

From the Airflow architecture:

1. **Task SDK** - Use this for DAG authoring (what users write)
2. **Operators execute in workers** - Never access metadata DB directly
3. **Use Hooks for external systems** - Separate connection logic
4. **XCom for task communication** - Don't use global state
5. **Execution API for worker-server communication** - Not metadata DB

## Next Steps

1. **Study existing operators** - Look at operators in `providers/*/operators/`
2. **Review the operator guide** - Check Airflow documentation
3. **Create provider-specific operators** - Put them in the appropriate provider package
4. **Add to production** - Follow the contribution guidelines in `CLAUDE.md`

## Real-World Examples to Study

- **BashOperator** - `providers/standard/src/airflow/providers/standard/operators/bash.py`
- **PythonOperator** - `providers/standard/src/airflow/providers/standard/operators/python.py`
- **S3 Operators** - `providers/amazon/src/airflow/providers/amazon/aws/operators/s3.py`
- **Sensor Operators** - Any `*Sensor` in provider packages

## Additional Resources

- [Airflow Documentation](https://airflow.apache.org/docs/)
- [Custom Operator Guide](https://airflow.apache.org/docs/apache-airflow/stable/howto/custom-operator.html)
- Contributing docs in `contributing-docs/`
- Project guidelines in `CLAUDE.md`
