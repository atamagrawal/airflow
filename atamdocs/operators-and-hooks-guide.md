# Operators and Hooks Deep Dive

> Comprehensive guide to understanding Airflow Operators and Hooks
>
> GitHub: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow

## Table of Contents

1. [Operators Overview](#operators-overview)
2. [Creating Custom Operators](#creating-custom-operators)
3. [Hooks Overview](#hooks-overview)
4. [Creating Custom Hooks](#creating-custom-hooks)
5. [Operator-Hook Pattern](#operator-hook-pattern)
6. [Advanced Topics](#advanced-topics)

---

## Operators Overview

**What is an Operator?**

An Operator is a task template - a class that defines a unit of work in your DAG.

**Key Characteristics:**
- Idempotent - Can be run multiple times with same result
- Atomic - Does one thing well
- Parameterizable - Accepts arguments for configuration

### Built-in Operator Types

**Location:** `airflow-core/src/airflow/operators/`

1. **Action Operators** - Execute operations
   - `BashOperator` - Run bash commands
   - `PythonOperator` - Execute Python functions
   - `EmailOperator` - Send emails

2. **Transfer Operators** - Move data between systems
   - Example: `S3ToRedshiftOperator`

3. **Sensors** - Wait for conditions
   - `FileSensor` - Wait for file to exist
   - `TimeSensor` - Wait for specific time
   - `ExternalTaskSensor` - Wait for another task

### Operator Base Classes

```
BaseOperator (base class for all)
    │
    ├── BashOperator
    │
    ├── PythonOperator
    │
    ├── BaseSensorOperator
    │       ├── FileSensor
    │       ├── TimeSensor
    │       └── ExternalTaskSensor
    │
    └── SQLExecuteQueryOperator
            └── (Provider-specific SQL operators)
```

---

## Creating Custom Operators

### Basic Structure

**File:** Your custom operator file

```python
from airflow.models.baseoperator import BaseOperator
from airflow.utils.decorators import apply_defaults
from typing import Any

class MyCustomOperator(BaseOperator):
    """
    Custom operator that does something specific

    :param my_param: Description of parameter
    :type my_param: str
    :param another_param: Another parameter
    :type another_param: int
    """

    # UI color for this operator in graph view
    ui_color = '#f0ede4'

    # Template fields - can use Jinja templates
    template_fields = ('my_param', 'templated_field')

    # Template extension - file extensions to template
    template_ext = ('.sql', '.txt')

    @apply_defaults
    def __init__(
        self,
        my_param: str,
        another_param: int = 10,
        *args,
        **kwargs
    ) -> None:
        super().__init__(*args, **kwargs)
        self.my_param = my_param
        self.another_param = another_param

    def execute(self, context: dict[str, Any]) -> Any:
        """
        This is the main method that gets called when task runs

        :param context: Task context dictionary
        :return: Return value (stored as XCom)
        """
        self.log.info(f"Executing with param: {self.my_param}")

        # Your custom logic here
        result = self._do_something()

        # Access context
        task_instance = context['task_instance']
        execution_date = context['execution_date']

        self.log.info(f"Completed at {execution_date}")

        # Return value stored as XCom
        return result

    def _do_something(self) -> str:
        """Helper method for your logic"""
        return f"Processed {self.my_param}"
```

### Using Your Custom Operator

```python
from airflow import DAG
from datetime import datetime
from my_operators import MyCustomOperator

with DAG(
    dag_id='example_custom_operator',
    start_date=datetime(2024, 1, 1),
    schedule='@daily',
) as dag:

    task = MyCustomOperator(
        task_id='my_custom_task',
        my_param='test_value',
        another_param=20,
    )
```

---

## Common Built-in Operators

### 1. PythonOperator

**File:** `airflow-core/src/airflow/operators/python.py`

```python
def my_python_function(ds, **kwargs):
    """
    :param ds: Execution date as string (YYYY-MM-DD)
    :param kwargs: Context variables
    """
    print(f"Running for date: {ds}")
    ti = kwargs['ti']

    # Push data to XCom
    ti.xcom_push(key='my_key', value='my_value')

    return 'Done'

task = PythonOperator(
    task_id='python_task',
    python_callable=my_python_function,
    provide_context=True,  # Pass context to function
    op_kwargs={'extra_param': 'value'},  # Extra arguments
    dag=dag,
)
```

**Key Features:**
- `python_callable`: Function to execute
- `op_args`: Positional arguments as list
- `op_kwargs`: Keyword arguments as dict
- `provide_context`: Pass Airflow context

### 2. BashOperator

**File:** `airflow-core/src/airflow/operators/bash.py`

```python
from airflow.operators.bash import BashOperator

task = BashOperator(
    task_id='bash_task',
    bash_command='echo "Hello from {{ ds }}"',  # Templated
    env={'ENV_VAR': 'value'},  # Environment variables
    append_env=True,  # Keep existing env vars
    dag=dag,
)

# Multi-line command
task2 = BashOperator(
    task_id='bash_script',
    bash_command="""
        cd /tmp
        ls -la
        echo "Done"
    """,
    dag=dag,
)
```

**Key Features:**
- `bash_command`: Command to execute (templated)
- `env`: Environment variables
- Return value: stdout captured as XCom

### 3. Sensors

**File:** `airflow-core/src/airflow/sensors/base.py`

```python
from airflow.sensors.filesystem import FileSensor

wait_for_file = FileSensor(
    task_id='wait_for_file',
    filepath='/path/to/file.txt',
    poke_interval=30,  # Check every 30 seconds
    timeout=600,  # Timeout after 10 minutes
    mode='poke',  # or 'reschedule'
    dag=dag,
)
```

**Sensor Modes:**
- `poke`: Keep worker busy, checking continuously
- `reschedule`: Free worker between checks (better for long waits)

**Creating Custom Sensor:**

```python
from airflow.sensors.base import BaseSensorOperator

class MyCustomSensor(BaseSensorOperator):
    """
    Sensor that checks custom condition
    """

    def __init__(self, my_param: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.my_param = my_param

    def poke(self, context: dict) -> bool:
        """
        Check if condition is met

        :return: True if condition met, False otherwise
        """
        self.log.info(f"Checking condition for {self.my_param}")

        # Your custom check logic
        condition_met = self._check_condition()

        if condition_met:
            self.log.info("Condition met!")
            return True
        else:
            self.log.info("Condition not met, will retry")
            return False

    def _check_condition(self) -> bool:
        """Your custom logic here"""
        return True  # or False
```

---

## Hooks Overview

**What is a Hook?**

A Hook is an interface to external systems (databases, APIs, cloud services). It handles:
- Connection management
- Authentication
- Common operations
- Connection pooling

**Key Difference:**
- **Operator** = What to do (task in DAG)
- **Hook** = How to connect (interface to external system)

### Built-in Hooks

**Location:** `airflow-core/src/airflow/hooks/`

Common hooks:
- `HttpHook` - HTTP/HTTPS requests
- `SqliteHook` - SQLite database
- Provider-specific hooks in `providers/` directory:
  - `PostgresHook` (providers/postgres)
  - `MySqlHook` (providers/mysql)
  - `S3Hook` (providers/amazon)
  - `GCSHook` (providers/google)

---

## Creating Custom Hooks

### Basic Hook Structure

**File:** Your custom hook file

```python
from airflow.hooks.base import BaseHook
from typing import Any
import requests

class MyCustomAPIHook(BaseHook):
    """
    Hook to interact with custom API

    :param my_custom_conn_id: Connection ID from Airflow connections
    :type my_custom_conn_id: str
    """

    conn_name_attr = 'my_custom_conn_id'
    default_conn_name = 'my_custom_default'
    conn_type = 'my_custom_api'
    hook_name = 'My Custom API'

    def __init__(self, my_custom_conn_id: str = default_conn_name):
        super().__init__()
        self.my_custom_conn_id = my_custom_conn_id
        self._session = None

    def get_conn(self) -> requests.Session:
        """
        Get connection to API

        :return: requests.Session object
        """
        if self._session is None:
            # Get connection from Airflow
            conn = self.get_connection(self.my_custom_conn_id)

            self._session = requests.Session()
            self._session.auth = (conn.login, conn.password)
            self._session.headers.update({
                'Content-Type': 'application/json',
            })

            # Use extra field from connection
            if conn.extra_dejson.get('api_key'):
                self._session.headers['X-API-Key'] = conn.extra_dejson['api_key']

        return self._session

    def get_data(self, endpoint: str) -> dict:
        """
        Fetch data from API endpoint

        :param endpoint: API endpoint
        :return: Response data
        """
        conn = self.get_connection(self.my_custom_conn_id)
        url = f"{conn.host}/{endpoint}"

        session = self.get_conn()
        response = session.get(url)
        response.raise_for_status()

        return response.json()

    def post_data(self, endpoint: str, data: dict) -> dict:
        """
        Post data to API endpoint

        :param endpoint: API endpoint
        :param data: Data to post
        :return: Response data
        """
        conn = self.get_connection(self.my_custom_conn_id)
        url = f"{conn.host}/{endpoint}"

        session = self.get_conn()
        response = session.post(url, json=data)
        response.raise_for_status()

        return response.json()
```

### Connection Configuration

**In Airflow UI:** Admin > Connections

```
Connection ID: my_custom_api_prod
Connection Type: my_custom_api
Host: https://api.example.com
Login: api_user
Password: api_password
Extra: {"api_key": "xyz123", "timeout": 30}
```

**In Code:**

```python
from airflow.models import Connection

conn = Connection(
    conn_id='my_custom_api_prod',
    conn_type='my_custom_api',
    host='https://api.example.com',
    login='api_user',
    password='api_password',
    extra='{"api_key": "xyz123"}',
)
```

---

## Operator-Hook Pattern

The standard pattern is: **Operator uses Hook**

### Example: Database Query Operator

```python
# Hook (handles connection)
from airflow.hooks.base import BaseHook
import psycopg2

class MyDatabaseHook(BaseHook):
    """Hook for custom database"""

    def __init__(self, conn_id: str):
        super().__init__()
        self.conn_id = conn_id

    def get_conn(self):
        """Get database connection"""
        conn_config = self.get_connection(self.conn_id)
        return psycopg2.connect(
            host=conn_config.host,
            port=conn_config.port,
            user=conn_config.login,
            password=conn_config.password,
            database=conn_config.schema,
        )

    def run_query(self, sql: str) -> list:
        """Execute SQL query"""
        conn = self.get_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            results = cursor.fetchall()
            conn.commit()
            return results
        finally:
            conn.close()

# Operator (defines task using hook)
from airflow.models.baseoperator import BaseOperator

class MyDatabaseOperator(BaseOperator):
    """Operator to run database query"""

    template_fields = ('sql',)
    template_ext = ('.sql',)

    def __init__(
        self,
        sql: str,
        database_conn_id: str = 'my_database_default',
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.sql = sql
        self.database_conn_id = database_conn_id

    def execute(self, context):
        """Execute the operator"""
        # Use hook for actual work
        hook = MyDatabaseHook(conn_id=self.database_conn_id)

        self.log.info(f"Executing SQL: {self.sql}")
        results = hook.run_query(self.sql)

        self.log.info(f"Query returned {len(results)} rows")
        return results

# Usage in DAG
with DAG('example_dag', ...) as dag:
    task = MyDatabaseOperator(
        task_id='query_database',
        sql='SELECT * FROM users WHERE created_date = {{ ds }}',
        database_conn_id='my_database_prod',
    )
```

**Benefits:**
- **Separation of concerns**: Operator = what, Hook = how
- **Reusability**: Hook can be used by multiple operators
- **Testability**: Can test hook and operator independently
- **Connection management**: Centralized in hook

---

## Advanced Topics

### 1. Templating in Operators

**Template Fields:**

```python
class MyOperator(BaseOperator):
    # These fields support Jinja templating
    template_fields = ('param1', 'param2', 'sql_query')

    # Files with these extensions will be templated
    template_ext = ('.sql', '.sh', '.txt')

    def __init__(self, param1, param2, sql_query, **kwargs):
        super().__init__(**kwargs)
        self.param1 = param1
        self.param2 = param2
        self.sql_query = sql_query

# Usage
task = MyOperator(
    task_id='templated_task',
    param1='{{ ds }}',  # Execution date
    param2='{{ macros.ds_add(ds, 7) }}',  # Date + 7 days
    sql_query='queries/my_query.sql',  # File will be templated
)
```

**Available Template Variables:**
```python
{
    'ds': '2024-01-01',  # Execution date YYYY-MM-DD
    'ds_nodash': '20240101',  # No dashes
    'ts': '2024-01-01T00:00:00+00:00',  # Timestamp
    'execution_date': datetime.datetime(...),
    'prev_execution_date': datetime.datetime(...),
    'next_execution_date': datetime.datetime(...),
    'dag': <DAG object>,
    'task': <Task object>,
    'task_instance': <TaskInstance object>,
    'ti': <TaskInstance object>,
    'params': {...},  # User-defined params
    'var': {  # Access Variables
        'value': lambda key: Variable.get(key),
        'json': lambda key: Variable.get(key, deserialize_json=True),
    },
    'macros': <module>,  # Utility functions
}
```

### 2. XCom Communication

**Push XCom:**

```python
class ProducerOperator(BaseOperator):
    def execute(self, context):
        # Method 1: Return value (stored as 'return_value')
        return {'key': 'value', 'number': 42}

        # Method 2: Explicit push
        context['task_instance'].xcom_push(
            key='custom_key',
            value='custom_value'
        )
```

**Pull XCom:**

```python
class ConsumerOperator(BaseOperator):
    def execute(self, context):
        ti = context['task_instance']

        # Pull return value from upstream task
        data = ti.xcom_pull(task_ids='producer_task')
        # data = {'key': 'value', 'number': 42}

        # Pull specific key
        custom = ti.xcom_pull(
            task_ids='producer_task',
            key='custom_key'
        )
        # custom = 'custom_value'

        # Pull from multiple tasks
        all_data = ti.xcom_pull(task_ids=['task1', 'task2'])
        # all_data = [result1, result2]
```

### 3. Dynamic Task Mapping (Airflow 2.3+)

```python
from airflow.decorators import task

@task
def generate_list():
    return [1, 2, 3, 4, 5]

@task
def process_item(item: int):
    return item * 2

with DAG('dynamic_mapping', ...) as dag:
    items = generate_list()

    # Creates 5 tasks dynamically
    results = process_item.expand(item=items)
```

### 4. Task Groups

```python
from airflow.utils.task_group import TaskGroup

with DAG('example_task_groups', ...) as dag:

    start = BashOperator(task_id='start', bash_command='echo start')

    with TaskGroup('group1') as group1:
        task1 = BashOperator(task_id='task1', bash_command='echo 1')
        task2 = BashOperator(task_id='task2', bash_command='echo 2')
        task1 >> task2

    with TaskGroup('group2') as group2:
        task3 = BashOperator(task_id='task3', bash_command='echo 3')
        task4 = BashOperator(task_id='task4', bash_command='echo 4')
        task3 >> task4

    end = BashOperator(task_id='end', bash_command='echo end')

    start >> [group1, group2] >> end
```

### 5. Deferrable Operators (Airflow 2.2+)

For long-running waits without blocking worker slots:

```python
from airflow.sensors.base import BaseSensorOperator
from airflow.triggers.temporal import TimeDeltaTrigger
from datetime import timedelta

class DeferrableTimeSensor(BaseSensorOperator):
    """
    Sensor that defers execution, freeing worker slot
    """

    def execute(self, context):
        # Defer to trigger
        self.defer(
            trigger=TimeDeltaTrigger(timedelta(seconds=60)),
            method_name='execute_complete'
        )

    def execute_complete(self, context, event=None):
        """Called when trigger fires"""
        self.log.info("Trigger fired, continuing execution")
        return event
```

**Benefits:**
- Worker slot freed during wait
- Better resource utilization
- Scales to thousands of waiting tasks

---

## Best Practices

### Operator Design

1. **Idempotency**
   ```python
   # Bad - creates duplicate records
   def execute(self, context):
       db.insert(record)

   # Good - checks before inserting
   def execute(self, context):
       if not db.exists(record_id):
           db.insert(record)
   ```

2. **Atomicity**
   ```python
   # Bad - multiple operations
   class MultiPurposeOperator(BaseOperator):
       def execute(self, context):
           self.download_data()
           self.transform_data()
           self.upload_data()

   # Good - single purpose
   class DownloadOperator(BaseOperator):
       def execute(self, context):
           self.download_data()
   ```

3. **Parameterization**
   ```python
   # Good - configurable
   class FlexibleOperator(BaseOperator):
       template_fields = ('input_path', 'output_path')

       def __init__(self, input_path, output_path, **kwargs):
           super().__init__(**kwargs)
           self.input_path = input_path
           self.output_path = output_path
   ```

### Hook Design

1. **Connection Management**
   ```python
   class WellDesignedHook(BaseHook):
       def __init__(self, conn_id):
           self.conn_id = conn_id
           self._conn = None

       def get_conn(self):
           if self._conn is None:
               self._conn = self._create_connection()
           return self._conn

       def __enter__(self):
           return self.get_conn()

       def __exit__(self, *args):
           if self._conn:
               self._conn.close()
   ```

2. **Error Handling**
   ```python
   def run_query(self, sql):
       try:
           conn = self.get_conn()
           cursor = conn.cursor()
           cursor.execute(sql)
           return cursor.fetchall()
       except DatabaseError as e:
           self.log.error(f"Query failed: {e}")
           raise AirflowException(f"Database query failed: {e}")
       finally:
           if conn:
               conn.close()
   ```

---

## Testing

### Testing Operators

```python
import pytest
from airflow.models import DAG
from datetime import datetime

def test_my_operator():
    dag = DAG('test_dag', start_date=datetime(2024, 1, 1))

    task = MyCustomOperator(
        task_id='test_task',
        my_param='test_value',
        dag=dag,
    )

    # Execute task
    result = task.execute(context={
        'execution_date': datetime(2024, 1, 1),
        'task_instance': Mock(),
    })

    assert result == expected_result
```

### Testing Hooks

```python
def test_my_hook(mocker):
    # Mock the connection
    mock_conn = mocker.patch(
        'airflow.hooks.base.BaseHook.get_connection'
    )
    mock_conn.return_value = Connection(
        conn_id='test',
        host='localhost',
        login='user',
        password='pass',
    )

    hook = MyCustomAPIHook(conn_id='test')
    result = hook.get_data('endpoint')

    assert result is not None
```

---

## Summary

**Operators:**
- Define tasks in your DAG
- Inherit from `BaseOperator`
- Implement `execute()` method
- Use template fields for dynamic values

**Hooks:**
- Interface to external systems
- Inherit from `BaseHook`
- Implement `get_conn()` method
- Handle authentication and connection pooling

**Pattern:**
- Operators use Hooks for external interactions
- Separation of "what" (operator) and "how" (hook)
- Reusable, testable, maintainable

**GitHub Links:**
- Operators: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow/operators
- Hooks: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow/hooks
- Base Classes: https://github.com/apache/airflow/blob/main/airflow-core/src/airflow/models/baseoperator.py

---

**Document Version**: 1.0
**Last Updated**: 2026-02-20
