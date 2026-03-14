# Sample Operator Examples - Quick Start

I've created a comprehensive tutorial on building Airflow operators with working examples and tests!

## What's Been Created

### 1. Two Sample Operators (`sample_data_filter_operator.py`)

#### `DataFilterOperator`
Filters numeric data based on a threshold and comparison operator.

**Features:**
- Supports multiple operators: `gte`, `lte`, `eq`, `gt`, `lt`
- Template fields for dynamic values (Jinja2)
- Can return filtered data or just count
- Handles various input formats (list, JSON string, CSV string)

**Quick Example:**
```python
from sample_data_filter_operator import DataFilterOperator

filter_task = DataFilterOperator(
    task_id="filter_high_values",
    data=[10, 25, 50, 75, 100],
    threshold=50,
    operator="gte",  # Keep values >= 50
)
# Result: [50, 75, 100]
```

#### `DataAggregationOperator`
Aggregates data with multiple statistical operations.

**Features:**
- Operations: `sum`, `mean`, `min`, `max`, `count`, `median`
- Returns dictionary with all results
- Handles edge cases (empty data, single value)

**Quick Example:**
```python
from sample_data_filter_operator import DataAggregationOperator

agg_task = DataAggregationOperator(
    task_id="aggregate_data",
    data=[10, 20, 30, 40, 50],
    operations=["sum", "mean", "max"],
)
# Result: {"sum": 150, "mean": 30.0, "max": 50}
```

### 2. Sample DAGs (`sample_dag_using_filter_operator.py`)

Two complete DAG examples showing:
- Task dependencies and chaining
- XCom data passing between tasks
- Using DAG configuration for dynamic values
- Integration with PythonOperator

### 3. Comprehensive Tests (`test_sample_data_filter_operator.py`)

27 tests covering:
- All operator types
- Edge cases (empty data, single value)
- Type conversions (string to numeric)
- Error conditions
- Parametrized testing

**All tests passing!** ✅

## Running the Examples

### Run the tests:
```bash
uv run --project airflow-core pytest dev/test_sample_data_filter_operator.py -xvs
```

### Run specific test:
```bash
uv run --project airflow-core pytest dev/test_sample_data_filter_operator.py::TestDataFilterOperator::test_basic_gte_filter -xvs
```

### Test the DAG (in Breeze):
```bash
breeze run airflow dags test example_data_filter_operators
```

## Key Patterns Demonstrated

### 1. Operator Structure
```python
class MyOperator(BaseOperator):
    template_fields: Sequence[str] = ("field1", "field2")
    ui_color = "#e8f7f0"

    def __init__(self, *, param1, param2, **kwargs):
        super().__init__(**kwargs)
        self.param1 = param1

    def execute(self, context: Context):
        # Your logic
        return result
```

### 2. Template Fields (Jinja2)
```python
# Dynamic values from upstream tasks
data="{{ ti.xcom_pull(task_ids='upstream') }}"

# Values from DAG config
threshold="{{ dag_run.conf.get('threshold', 50) }}"
```

### 3. XCom Communication
```python
# Automatically pushed by return value
def execute(self, context: Context):
    return [1, 2, 3]

# Pull in downstream task
def process(**context):
    data = context["ti"].xcom_pull(task_ids="upstream")
```

### 4. Testing with Pytest
```python
@pytest.mark.parametrize("input,expected", [(10, 20), (20, 40)])
def test_multiple_cases(self, input, expected):
    operator = MyOperator(task_id="test", value=input)
    result = operator.execute({})
    assert result == expected
```

### 5. Logging
```python
self.log.info("Processing %d items", count)
self.log.warning("Skipping invalid item")
```

## Files Overview

| File | Purpose | Lines |
|------|---------|-------|
| `sample_data_filter_operator.py` | Two example operators | ~340 |
| `sample_dag_using_filter_operator.py` | Example DAGs | ~140 |
| `test_sample_data_filter_operator.py` | Test suite | ~380 |
| `SAMPLE_OPERATOR_TUTORIAL.md` | Detailed tutorial | ~530 |

## What You Learn

1. **Operator Development**
   - Inheriting from BaseOperator
   - Constructor patterns
   - Execute method implementation
   - Template fields

2. **Testing**
   - Pytest patterns (not unittest)
   - Parametrized tests
   - Edge case testing
   - Error validation

3. **DAG Integration**
   - Using custom operators
   - Task dependencies
   - XCom data flow
   - Dynamic configuration

4. **Best Practices**
   - Type hints
   - Proper logging
   - Error handling
   - Documentation

## Next Steps

1. **Explore the code** - Read through the operators to understand patterns
2. **Run the tests** - See how everything works
3. **Modify the examples** - Add your own logic
4. **Create your operator** - Use this as a template
5. **Read the full tutorial** - Check `SAMPLE_OPERATOR_TUTORIAL.md`

## Real-World Examples

For production operator patterns, study these in the codebase:
- `providers/standard/src/airflow/providers/standard/operators/bash.py` - BashOperator
- `providers/standard/src/airflow/providers/standard/operators/python.py` - PythonOperator
- `providers/amazon/src/airflow/providers/amazon/aws/operators/s3.py` - S3 operators

## Architecture Notes

Remember Airflow's boundaries:
- **Workers execute tasks** - Don't access metadata DB directly
- **Use Hooks for external systems** - Separate connection logic
- **XCom for task communication** - Not global state
- **Execution API for communication** - Not metadata DB

## Questions?

Check the detailed tutorial: `SAMPLE_OPERATOR_TUTORIAL.md`

Happy coding! 🚀
