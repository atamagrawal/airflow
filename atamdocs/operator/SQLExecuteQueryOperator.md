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

### 2. OpenLineage Integration & Lineage Extraction

SQLExecuteQueryOperator automatically generates OpenLineage facets for data lineage tracking when the OpenLineage provider is installed.

#### How Lineage Extraction Works

The operator has two methods for extracting lineage:

**1. `get_openlineage_facets_on_start()` - SQL Parse-time Lineage**

Called before task execution to extract lineage from the SQL statement:

```python
def get_openlineage_facets_on_start(self) -> OperatorLineage | None:
    """Generate OpenLineage facets on start for SQL operators."""
```

**Process:**
1. Retrieves the SQL statement from the operator
2. Gets the database hook and connection information
3. Creates a `SQLParser` with the database dialect
4. Parses SQL to extract:
   - **Input datasets**: Tables/views being SELECT'ed or joined FROM
   - **Output datasets**: Tables being INSERT'ed/UPDATE'ed/DELETE'ed
   - **Schema information**: Column names and types from cursor descriptions
5. Returns `OperatorLineage` object with extracted metadata

**Example Input SQL:**
```sql
INSERT INTO target_table
SELECT id, name, email
FROM source_table
WHERE status = 'active'
```

**Extracted Lineage:**
- **Input Dataset**: `source_table`
- **Output Dataset**: `target_table`
- **Schema**: Columns from cursor description

**2. `get_openlineage_facets_on_complete()` - Database-Specific Lineage**

Called after task execution to extract additional database-specific lineage metadata:

```python
def get_openlineage_facets_on_complete(self, task_instance) -> OperatorLineage | None:
    """Generate OpenLineage facets when task completes."""
```

**Process:**
1. Calls `get_openlineage_facets_on_start()` for SQL-based lineage
2. Calls hook's `get_openlineage_database_specific_lineage()` for database-specific info
3. Merges both lineage sources:
   - Combines inputs and outputs
   - Merges run facets (execution metadata)
   - Merges job facets (job metadata)

#### Lineage Information Extracted

The operator extracts the following lineage information:

| Information | Source | Details |
|-------------|--------|---------|
| **Input Datasets** | SQL Parser | Tables/views used in FROM/JOIN clauses |
| **Output Datasets** | SQL Parser | Tables modified by INSERT/UPDATE/DELETE |
| **Database Connection** | Database Hook | Host, port, database name, connection type |
| **Database Dialect** | Database Hook | SQL dialect (PostgreSQL, MySQL, MSSQL, etc.) |
| **Schema Information** | Cursor Description | Column names and types from query results |
| **Database-Specific Info** | Hook Implementation | Database-specific lineage (views, functions, etc.) |

#### Requirements for Lineage Extraction

For lineage to be automatically extracted:

1. **OpenLineage Provider Installed**
   ```bash
   pip install apache-airflow-providers-openlineage
   ```

2. **Database Provider with OpenLineage Support**
   - Must implement `DbApiHook` (all common-sql providers do)
   - Must implement:
     - `get_openlineage_database_info(connection)`
     - `get_openlineage_database_dialect(connection)`
     - `get_openlineage_default_schema()`
     - `get_sqlalchemy_engine()`

3. **Valid SQL Statement**
   - SQL must be parseable by the database dialect
   - Must contain FROM/INSERT/UPDATE/DELETE clauses

#### Example with Lineage Tracking

```python
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.utils.timezone import datetime

with DAG('lineage_tracking', start_date=datetime(2021, 1, 1)) as dag:
    # This operator will automatically extract lineage
    extract_data = SQLExecuteQueryOperator(
        task_id='extract_data',
        conn_id='postgres_db',
        sql="""
            -- Input: source_table (extracted as input dataset)
            -- Output: staging_table (extracted as output dataset)
            INSERT INTO staging_table (id, name, created_at)
            SELECT user_id, user_name, registration_date
            FROM source_table
            WHERE status = 'active'
        """,
    )
```

**Extracted Lineage Metadata:**
```
Input Datasets:
  - postgres_db.public.source_table

Output Datasets:
  - postgres_db.public.staging_table

Job Metadata:
  - Database: postgres_db
  - Dialect: postgresql
  - Host: localhost
  - Port: 5432
```

#### Lineage with Multiple SQL Statements

When using `split_statements=True` with multiple SQL statements:

```python
multi_statement = SQLExecuteQueryOperator(
    task_id='multi_sql',
    conn_id='postgres_db',
    sql=[
        "DELETE FROM staging_table WHERE created_at < NOW() - INTERVAL 30 DAY;",
        "INSERT INTO staging_table SELECT * FROM raw_table;",
        "SELECT COUNT(*) FROM staging_table;"
    ],
    split_statements=True,
    return_last=False,
)
```

**Lineage Extracted:**
- First statement: DELETE from `staging_table`
- Second statement: INSERT INTO `staging_table`, SELECT FROM `raw_table`
- Third statement: SELECT FROM `staging_table`

**Combined Lineage:**
- **Inputs**: `raw_table`, `staging_table` (read in SELECT)
- **Outputs**: `staging_table` (modified by DELETE and INSERT)

#### Viewing Lineage in Airflow UI

Once lineage is extracted:

1. **Lineage Graph Tab**: Shows visual data flow between tasks
2. **OpenLineage Integration**: Data lineage visible in OpenLineage servers
3. **XCom Push**: Task outputs available to downstream tasks

#### Troubleshooting Lineage Extraction

**Issue: Lineage Not Appearing**

1. **Check OpenLineage Provider Installation**
   ```bash
   pip list | grep openlineage
   ```

2. **Enable Debug Logging**
   ```python
   # In logging config
   'airflow.providers.common.sql.operators.sql': 'DEBUG'
   ```

3. **Verify Database Hook Support**
   - Ensure database provider version supports OpenLineage
   - Check hook implements required methods

4. **Check SQL Syntax**
   - SQL must be valid for the database dialect
   - Must contain FROM/INSERT/UPDATE/DELETE clauses

**Issue: Partial Lineage (Some Datasets Missing)**

- **Template Variables**: SQL with Jinja2 variables may not parse correctly
  - Solution: Verify SQL is rendered before parsing
- **Complex Queries**: Subqueries, CTEs may not be fully parsed
  - Solution: Check database-specific dialect support
- **Database-Specific Features**: Some constructs may not be recognized
  - Solution: Update database provider to latest version

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

## Technical Deep-Dive: Lineage Extraction Internals

### Code Flow During Task Execution

When a task using `SQLExecuteQueryOperator` executes, the lineage extraction follows this flow:

```
Task Execution Starts
    ↓
1. get_openlineage_facets_on_start() called
    ↓
    a. Import OperatorLineage and SQLParser
    b. Get SQL from operator.sql
    c. Get database hook via self.get_db_hook()
    d. Create SQLParser with:
       - dialect = hook.get_openlineage_database_dialect(connection)
       - default_schema = hook.get_openlineage_default_schema()
    e. Call sql_parser.generate_openlineage_metadata_from_sql():
       - Parse SQL statement
       - Extract table names from FROM/JOIN/INSERT/UPDATE/DELETE
       - Extract column information from cursor description
       - Return OperatorLineage with inputs and outputs
    ↓
2. Task executes (execute() method runs)
    ↓
3. get_openlineage_facets_on_complete() called
    ↓
    a. Call get_openlineage_facets_on_start() again
    b. Call hook.get_openlineage_database_specific_lineage(task_instance)
    c. Merge both lineage sources:
       - Combine input datasets
       - Combine output datasets
       - Merge run facets and job facets
    ↓
Lineage metadata sent to OpenLineage server
```

### SQL Parsing Mechanism

#### Input: SQL Statement

```python
sql = """
INSERT INTO staging_users (user_id, username, email)
SELECT
    u.id,
    u.name,
    u.email
FROM raw_users u
JOIN user_metadata m ON u.id = m.user_id
WHERE u.created_date >= '2024-01-01'
"""
```

#### Parsing Steps

1. **Tokenize**: Break SQL into tokens
2. **Parse AST**: Create Abstract Syntax Tree
3. **Extract SELECT Parts**:
   - **FROM clause**: `raw_users` (table 1)
   - **JOIN clause**: `user_metadata` (table 2)
4. **Extract INSERT Parts**:
   - **INTO clause**: `staging_users` (output table)
   - **Columns**: `user_id, username, email`
5. **Extract Schema**:
   - From cursor description: `[(user_id, INT), (username, VARCHAR), (email, VARCHAR)]`

#### Output: Lineage Metadata

```python
OperatorLineage(
    inputs=[
        Dataset(
            name='raw_users',
            namespace='postgres://localhost:5432',
            facets={
                'schema': SchemaDatasetFacet(fields=[
                    SchemaDatasetFacetFields('user_id', 'INT'),
                    SchemaDatasetFacetFields('name', 'VARCHAR'),
                    SchemaDatasetFacetFields('email', 'VARCHAR'),
                ])
            }
        ),
        Dataset(
            name='user_metadata',
            namespace='postgres://localhost:5432',
            facets={...}
        ),
    ],
    outputs=[
        Dataset(
            name='staging_users',
            namespace='postgres://localhost:5432',
            facets={
                'schema': SchemaDatasetFacet(fields=[
                    SchemaDatasetFacetFields('user_id', 'INT'),
                    SchemaDatasetFacetFields('username', 'VARCHAR'),
                    SchemaDatasetFacetFields('email', 'VARCHAR'),
                ])
            }
        ),
    ],
    job_facets={
        'sql': SQLJobFacet(query='INSERT INTO staging_users...'),
    }
)
```

### Hook Integration Points

The operator relies on the database hook to provide dialect and connection information:

```python
# 1. Get database information
database_info = hook.get_openlineage_database_info(connection)
# Returns: DatabaseInfo(database, host, port, user, schema)

# 2. Get SQL dialect for parsing
dialect = hook.get_openlineage_database_dialect(connection)
# Returns: "postgresql", "mysql", "mssql", etc.

# 3. Get default schema
default_schema = hook.get_openlineage_default_schema()
# Returns: "public" (for PostgreSQL), "dbo" (for MSSQL), etc.

# 4. Get SQLAlchemy engine for advanced operations
engine = hook.get_sqlalchemy_engine()
# Returns: SQLAlchemy engine instance for additional introspection

# 5. Get database-specific lineage (optional)
db_lineage = hook.get_openlineage_database_specific_lineage(task_instance)
# Returns: Additional lineage metadata specific to the database
```

### Error Handling During Lineage Extraction

The operator gracefully handles errors:

```python
try:
    from airflow.providers.openlineage.extractors import OperatorLineage
    from airflow.providers.openlineage.sqlparser import SQLParser
except ImportError:
    self.log.debug("OpenLineage could not import required classes. Skipping.")
    return None  # No lineage if provider not installed

if database_info is None:
    self.log.debug("OpenLineage could not retrieve database information. Skipping.")
    return OperatorLineage()  # Return empty lineage

try:
    sql_parser = SQLParser(...)
except AttributeError:
    self.log.debug("%s failed to get database dialect", hook)
    return None  # Return None if SQL parser can't be created
```

### Example Execution Trace

**DAG Definition:**
```python
extract_task = SQLExecuteQueryOperator(
    task_id='extract_from_source',
    conn_id='postgres_prod',
    sql="INSERT INTO warehouse.dim_users SELECT * FROM raw.users WHERE active = true;",
)
```

**Execution Trace:**

```
1. Task starts: extract_from_source

2. get_openlineage_facets_on_start() called:
   - SQL: "INSERT INTO warehouse.dim_users SELECT * FROM raw.users WHERE active = true"
   - Hook: PostgresHook (postgres_prod connection)
   - Dialect: postgresql
   - Parse SQL:
     * Input: raw.users
     * Output: warehouse.dim_users
   - Extract schema from cursor description
   - Create OperatorLineage object

3. execute() called:
   - Connect to postgres_prod via PostgresHook
   - Run INSERT query
   - Fetch results (if required)
   - Process output

4. get_openlineage_facets_on_complete() called:
   - Get lineage from step 2
   - Get database-specific lineage from hook
   - Merge both
   - Return complete OperatorLineage

5. Lineage published to OpenLineage server:
   {
     "eventType": "COMPLETE",
     "inputs": [{
       "name": "postgres_prod.raw.users",
       "namespace": "postgres://prod.company.com:5432"
     }],
     "outputs": [{
       "name": "postgres_prod.warehouse.dim_users",
       "namespace": "postgres://prod.company.com:5432"
     }],
     "runId": "execute_from_source_20240222_abc123",
     "jobName": "dag_name.extract_task",
     "jobNamespace": "airflow://airflow_host"
   }
```

### Supported SQL Patterns for Lineage Extraction

The SQLParser can extract lineage from:

| Pattern | Input Tables | Output Tables | Example |
|---------|--------------|---------------|---------|
| SELECT | FROM, JOIN clauses | None | `SELECT * FROM users` |
| INSERT INTO...SELECT | FROM, JOIN clauses | INSERT destination | `INSERT INTO audit SELECT * FROM logs` |
| UPDATE...FROM | UPDATE table, FROM joins | UPDATE table | `UPDATE users SET status = 'active' FROM user_meta WHERE...` |
| DELETE FROM...USING | DELETE table, USING joins | DELETE table | `DELETE FROM logs USING archive WHERE date < '2020-01-01'` |
| CTEs (WITH clauses) | Referenced tables in CTE | Referenced tables | `WITH tmp AS (SELECT * FROM source) SELECT * FROM tmp` |
| Subqueries | Tables in subqueries | Output table | `INSERT INTO target SELECT * FROM (SELECT * FROM source) sub` |
| UNION/INTERSECT | All tables in all queries | Combined output | `SELECT * FROM table1 UNION SELECT * FROM table2` |

### Limitations and Considerations

1. **Complex Nested Queries**: Deeply nested subqueries may not be fully parsed
2. **Dynamic SQL**: SQL generated at runtime cannot be parsed statically
3. **Stored Procedures**: If calling procedures, lineage won't capture internal tables accessed
4. **Views**: View references are captured, but underlying tables depend on view definitions
5. **Database-Specific Features**: Some proprietary SQL extensions may not parse correctly
6. **Schema Inference**: Without explicit schema qualification, default schema is assumed

## Summary

`SQLExecuteQueryOperator` is a powerful, flexible SQL execution operator that:
- ✅ Works with any database provider
- ✅ Handles single or multiple SQL statements
- ✅ Provides result processing capabilities
- ✅ Integrates with Airflow's XCom and templating
- ✅ Supports data lineage tracking (OpenLineage)
- ✅ Enables secure parameterized queries

It's the standard choice for SQL execution tasks in Apache Airflow when using the common-sql provider.

