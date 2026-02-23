# SQLExecuteQueryOperator - Comprehensive Guide

## Overview

`SQLExecuteQueryOperator` is an Airflow operator provided by the `airflow-common-sql` provider that executes SQL queries against various databases. It's a flexible, database-agnostic operator that works with any database that has a corresponding Airflow provider.

## Key Characteristics

- **Location**: `airflow.providers.common.sql.operators.sql.SQLExecuteQueryOperator`
- **Inheritance**: Extends `BaseSQLOperator`
- **Database Support**: Works with any database provider that implements `DbApiHook`
- **Flexibility**: Supports single or multiple SQL statements, with various output handling options

## Constructor Parameters

### Core Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sql` | `str \| list[str]` | Required | Single SQL string, list of SQL strings, or path to `.sql` template file to execute |
| `conn_id` | `str \| None` | `None` | Airflow connection ID to the database |
| `database` | `str \| None` | `None` | Database/schema name (overrides the one in connection) |

### Execution Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `autocommit` | `bool` | `False` | If `True`, each command is automatically committed |
| `parameters` | `Mapping \| Iterable \| None` | `None` | Parameters to render/bind in the SQL query (templated) |
| `split_statements` | `bool \| None` | `None` | If `True`, splits SQL string into separate statements and runs them individually |
| `return_last` | `bool` | `True` | If `True`, returns only the result of the last statement (when `split_statements=True`) |

### Output Handling Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `handler` | `Callable` | `fetch_all_handler` | Function applied to cursor to process results |
| `output_processor` | `Callable \| None` | `default_output_processor` | Function to process the final output before returning |
| `show_return_value_in_logs` | `bool` | `False` | If `True`, logs the operator output (caution with large datasets) |
| `requires_result_fetch` | `bool` | `False` | If `True`, ensures query results are fetched before completing |
| `do_xcom_push` | `bool` | `True` (inherited) | If `True`, pushes results to XCom for downstream tasks |

## How It Works

### Execution Flow

1. **Connection**: Connects to database using the specified `conn_id`
2. **Templating**: Renders template variables in SQL and parameters
3. **Execution**: Runs SQL through the database hook
4. **Processing**: Applies handler and output processor to results
5. **Return**: Returns processed results (or `None` if not pushing to XCom)

### Template Fields

- `sql` - Supports Jinja2 templating
- `parameters` - Supports JSON templating
- `conn_id` - Supports templating
- `database` - Supports templating
- `hook_params` - Supports templating

Template files with `.sql` or `.json` extensions are automatically rendered.

## Common Use Cases

### 1. Simple SELECT Query

```python
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.utils.timezone import datetime

with DAG('simple_query', start_date=datetime(2021, 1, 1)) as dag:
    query = SQLExecuteQueryOperator(
        task_id='select_data',
        conn_id='my_database',
        sql='SELECT * FROM users LIMIT 10;',
        do_xcom_push=True,  # Push results to XCom
    )
```

### 2. Multiple Statements with return_last

```python
execute_query = SQLExecuteQueryOperator(
    task_id='multiple_queries',
    conn_id='my_database',
    sql=[
        'DELETE FROM staging_table WHERE date < NOW() - INTERVAL 30 DAY;',
        'INSERT INTO staging_table SELECT * FROM source_table;',
        'SELECT COUNT(*) FROM staging_table;'
    ],
    split_statements=True,
    return_last=True,  # Only return result of the last SELECT
)
```

### 3. Query with Parameters (SQL Injection Prevention)

```python
execute_query = SQLExecuteQueryOperator(
    task_id='parameterized_query',
    conn_id='my_database',
    sql='SELECT * FROM users WHERE user_id = %s AND status = %s;',
    parameters={'user_id': 123, 'status': 'active'},
)
```

### 4. Template File

Create `query.sql`:
```sql
SELECT * FROM {{ table_name }}
WHERE created_date >= '{{ execution_date }}'
```

Then in DAG:
```python
execute_query = SQLExecuteQueryOperator(
    task_id='from_template',
    conn_id='my_database',
    sql='/path/to/query.sql',  # Automatically templated
)
```

### 5. No Output (Data Modification Only)

```python
execute_query = SQLExecuteQueryOperator(
    task_id='data_load',
    conn_id='my_database',
    sql='INSERT INTO table SELECT * FROM source;',
    do_xcom_push=False,  # Don't push results
)
```

## Output Behavior

### When `do_xcom_push=True` (default for execution)

**Single SQL Statement or `return_last=True`**:
- Returns: The query result (list of rows)
- Type: Same as what the `handler` function returns (typically `list[tuple]`)

**Multiple Statements with `return_last=False`**:
- Returns: List of results, one per statement
- Example: `[[row1, row2], [row3, row4]]`

### When `do_xcom_push=False`

- Returns: `None`
- Results are not available to downstream tasks via XCom

## Handler Functions

The `handler` parameter specifies how to process cursor results:

### Built-in Handlers

**`fetch_all_handler`** (default)
```python
def fetch_all_handler(cursor):
    return cursor.fetchall()  # Returns all rows as list of tuples
```

**`fetch_one_handler`**
```python
def fetch_one_handler(cursor):
    return cursor.fetchone()  # Returns first row only
```

### Custom Handlers

```python
def custom_handler(cursor):
    # Process cursor and return custom format
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

execute_query = SQLExecuteQueryOperator(
    task_id='custom_handler_task',
    conn_id='my_database',
    sql='SELECT * FROM users;',
    handler=custom_handler,
)
```

## Output Processor Functions

Process results after handler execution:

### Default Processor
```python
def default_output_processor(results, descriptions):
    return results  # Returns results as-is
```

### Custom Processor

```python
def custom_output_processor(results, descriptions):
    # results: list of query results
    # descriptions: list of column descriptions from cursor.description
    # Process and return modified results
    return process_results(results, descriptions)

execute_query = SQLExecuteQueryOperator(
    task_id='custom_processor_task',
    conn_id='my_database',
    sql='SELECT * FROM users;',
    output_processor=custom_output_processor,
)
```

## Real-World Example (from Airflow docs)

```python
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.utils.timezone import datetime

AIRFLOW_DB_METADATA_TABLE = "ab_role"
connection_args = {
    "conn_id": "airflow_db",
    "conn_type": "Postgres",
    "host": "postgres",
    "schema": "postgres",
    "login": "postgres",
    "password": "postgres",
    "port": 5432,
}

with DAG(
    "example_sql_execute_query",
    description="Example DAG for SQLExecuteQueryOperator.",
    default_args=connection_args,
    start_date=datetime(2021, 1, 1),
    schedule=None,
    catchup=False,
) as dag:
    execute_query = SQLExecuteQueryOperator(
        task_id="execute_query",
        sql=f"SELECT 1; SELECT * FROM {AIRFLOW_DB_METADATA_TABLE} LIMIT 1;",
        split_statements=True,
        return_last=False,
        do_xcom_push=True,
    )
```

**In this example**:
- `split_statements=True`: Runs both SELECT statements separately
- `return_last=False`: Returns results from BOTH statements, not just the last one
- `do_xcom_push=True`: Results pushed to XCom for downstream tasks
- Returns: `[[1], [ab_role_record]]` - two results, one for each statement

## Advanced Features

### 1. XCom Integration

```python
task1 = SQLExecuteQueryOperator(
    task_id='get_user_id',
    conn_id='my_database',
    sql='SELECT id FROM users WHERE name = %s;',
    parameters={'name': 'John'},
    do_xcom_push=True,
)

# Downstream task uses XCom
task2 = SQLExecuteQueryOperator(
    task_id='get_user_details',
    conn_id='my_database',
    sql='SELECT * FROM users WHERE id = %s;',
    parameters=[task1.output],  # References task1's XCom
)
```

### 2. OpenLineage Integration

SQLExecuteQueryOperator automatically generates OpenLineage facets for data lineage tracking when the OpenLineage provider is installed. It extracts:
- Input datasets
- Output datasets
- Database connection information

### 3. Custom Database Hook Integration

The operator automatically selects the correct hook based on `conn_id` connection type. Supported databases include:
- PostgreSQL
- MySQL
- SQLite
- MSSQL
- Oracle
- Snowflake
- BigQuery
- And many more via provider ecosystem

## Key Differences from Other SQL Operators

### vs `BaseSQLOperator`
- `BaseSQLOperator`: Base class, only provides database connection
- `SQLExecuteQueryOperator`: Full-featured executor with result handling

### vs `SQLColumnCheckOperator` / `SQLTableCheckOperator`
- `SQLExecuteQueryOperator`: Executes arbitrary SQL
- Check operators: Specialized for data quality checks with predefined check types

## Troubleshooting

### No Results Returned
- Check: `do_xcom_push=True` is set
- Check: `handler` is not `None`
- Check: Query actually returns results

### Results Not Visible in Logs
- Set: `show_return_value_in_logs=True`
- ⚠️ Warning: Don't use with large datasets

### SQL Not Being Templated
- Ensure SQL contains `{{ variable }}` syntax
- Check: File has `.sql` or `.json` extension for file-based SQL
- Check: `parameters` is passed for parameter binding

### Wrong Database Selected
- Verify: `conn_id` references correct connection
- Verify: `database` parameter is set if needed
- Check: Connection configuration in Airflow UI

## Performance Considerations

1. **Large Result Sets**: Use `handler=None` if not processing results to avoid memory overhead
2. **Multiple Statements**: Use `split_statements=True` for independent statements to enable better error handling
3. **Parameterized Queries**: Always use `parameters` for bound variables, never string interpolation
4. **Logging**: Disable `show_return_value_in_logs` for production with large result sets

## Summary

`SQLExecuteQueryOperator` is a powerful, flexible SQL execution operator that:
- ✅ Works with any database provider
- ✅ Handles single or multiple SQL statements
- ✅ Provides result processing capabilities
- ✅ Integrates with Airflow's XCom and templating
- ✅ Supports data lineage tracking (OpenLineage)
- ✅ Enables secure parameterized queries

It's the standard choice for SQL execution tasks in Apache Airflow when using the common-sql provider.

