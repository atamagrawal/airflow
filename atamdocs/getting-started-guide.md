# Getting Started with Airflow Development

> Practical guide to get you up and running with Apache Airflow development
>
> GitHub: https://github.com/apache/airflow

## Table of Contents

1. [Quick Start](#quick-start)
2. [Setting Up Development Environment](#setting-up-development-environment)
3. [Your First DAG](#your-first-dag)
4. [Understanding the Workflow](#understanding-the-workflow)
5. [Common Commands](#common-commands)
6. [Debugging Tips](#debugging-tips)
7. [Next Steps](#next-steps)

---

## Quick Start

### Prerequisites

- Python 3.8+ installed
- Basic understanding of Python
- Command line familiarity

### 5-Minute Setup

```bash
# 1. Create virtual environment
python -m venv airflow_venv
source airflow_venv/bin/activate  # On Windows: airflow_venv\Scripts\activate

# 2. Set Airflow home (where config and DAGs will live)
export AIRFLOW_HOME=~/airflow

# 3. Install Airflow
pip install apache-airflow

# 4. Initialize database
airflow db init

# 5. Create admin user
airflow users create \
    --username admin \
    --firstname Admin \
    --lastname User \
    --role Admin \
    --email admin@example.com \
    --password admin

# 6. Start webserver (in terminal 1)
airflow webserver --port 8080

# 7. Start scheduler (in terminal 2)
airflow scheduler

# 8. Open browser
# Navigate to: http://localhost:8080
# Login: admin / admin
```

---

## Setting Up Development Environment

### Project Structure

After initialization, your `$AIRFLOW_HOME` looks like:

```
~/airflow/
├── airflow.cfg          # Main configuration file
├── airflow.db           # SQLite database (default)
├── dags/                # Your DAG files go here
│   └── example_dag.py
├── logs/                # Task execution logs
│   └── dag_id/
│       └── task_id/
│           └── execution_date/
│               └── 1.log
├── plugins/             # Custom plugins
└── airflow-webserver.pid
```

### Configuration File

**Location:** `~/airflow/airflow.cfg`

**Key Settings:**

```ini
[core]
# Where DAG files are stored
dags_folder = /Users/you/airflow/dags

# Executor type
executor = LocalExecutor  # or SequentialExecutor, CeleryExecutor

# Database connection
sql_alchemy_conn = sqlite:////Users/you/airflow/airflow.db
# For PostgreSQL: postgresql+psycopg2://user:pass@localhost/airflow

# Parallelism - max tasks that can run across all DAGs
parallelism = 32

# Max active runs per DAG
max_active_runs_per_dag = 16

[scheduler]
# How often to scan for new/changed DAG files (seconds)
dag_dir_list_interval = 300

# How many DagRuns to create per loop
max_dagruns_to_create_per_loop = 10

[webserver]
# Web UI port
web_server_port = 8080

# Expose configuration in UI
expose_config = True
```

### Database Setup (PostgreSQL - Recommended for Production)

```bash
# 1. Install PostgreSQL driver
pip install psycopg2-binary

# 2. Create database
createdb airflow

# 3. Update airflow.cfg
# sql_alchemy_conn = postgresql+psycopg2://user:password@localhost/airflow

# 4. Initialize
airflow db init
```

---

## Your First DAG

### Example 1: Hello World DAG

**File:** `~/airflow/dags/hello_world_dag.py`

```python
from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

# Define default arguments
default_args = {
    'owner': 'atam',
    'depends_on_past': False,
    'email': ['your-email@example.com'],
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Define the DAG
dag = DAG(
    'hello_world_dag',
    default_args=default_args,
    description='A simple Hello World DAG',
    schedule=timedelta(days=1),  # Run daily
    start_date=datetime(2024, 1, 1),
    catchup=False,  # Don't backfill
    tags=['example', 'hello-world'],
)

# Task 1: Bash command
task_1 = BashOperator(
    task_id='print_date',
    bash_command='date',
    dag=dag,
)

# Task 2: Python function
def say_hello(**context):
    print("Hello from Airflow!")
    print(f"Execution date: {context['execution_date']}")
    return "Hello World!"

task_2 = PythonOperator(
    task_id='say_hello',
    python_callable=say_hello,
    provide_context=True,
    dag=dag,
)

# Task 3: Another bash command
task_3 = BashOperator(
    task_id='print_goodbye',
    bash_command='echo "Goodbye!"',
    dag=dag,
)

# Set task dependencies
task_1 >> task_2 >> task_3  # Linear: 1 -> 2 -> 3
```

### Example 2: Data Pipeline DAG

**File:** `~/airflow/dags/data_pipeline_dag.py`

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta
import pandas as pd

default_args = {
    'owner': 'atam',
    'start_date': datetime(2024, 1, 1),
    'retries': 2,
}

dag = DAG(
    'data_pipeline',
    default_args=default_args,
    description='ETL pipeline example',
    schedule='@daily',
    catchup=False,
)

def extract_data(**context):
    """Extract data from source"""
    # Simulate data extraction
    data = {
        'id': [1, 2, 3, 4, 5],
        'name': ['Alice', 'Bob', 'Charlie', 'David', 'Eve'],
        'value': [100, 200, 150, 300, 250]
    }
    df = pd.DataFrame(data)

    # Save to temporary location
    df.to_csv('/tmp/raw_data.csv', index=False)
    print(f"Extracted {len(df)} rows")

    # Push count to XCom
    context['task_instance'].xcom_push(key='row_count', value=len(df))

def transform_data(**context):
    """Transform the data"""
    # Read from temporary location
    df = pd.read_csv('/tmp/raw_data.csv')

    # Transform: add calculated column
    df['value_doubled'] = df['value'] * 2

    # Save transformed data
    df.to_csv('/tmp/transformed_data.csv', index=False)
    print(f"Transformed {len(df)} rows")

def load_data(**context):
    """Load data to destination"""
    # Read transformed data
    df = pd.read_csv('/tmp/transformed_data.csv')

    # Load to destination (simulated)
    print(f"Loading {len(df)} rows to database...")
    print(df.head())

    # Get row count from extract task
    row_count = context['task_instance'].xcom_pull(
        task_ids='extract',
        key='row_count'
    )
    print(f"Original row count from extract: {row_count}")

def validate_data(**context):
    """Validate the loaded data"""
    df = pd.read_csv('/tmp/transformed_data.csv')

    # Simple validation
    assert len(df) > 0, "No data loaded!"
    assert 'value_doubled' in df.columns, "Missing transformed column!"

    print("Data validation passed!")

# Define tasks
extract_task = PythonOperator(
    task_id='extract',
    python_callable=extract_data,
    dag=dag,
)

transform_task = PythonOperator(
    task_id='transform',
    python_callable=transform_data,
    dag=dag,
)

load_task = PythonOperator(
    task_id='load',
    python_callable=load_data,
    dag=dag,
)

validate_task = PythonOperator(
    task_id='validate',
    python_callable=validate_data,
    dag=dag,
)

# Set dependencies: Extract -> Transform -> Load -> Validate
extract_task >> transform_task >> load_task >> validate_task
```

### Example 3: Branching DAG

**File:** `~/airflow/dags/branching_dag.py`

```python
from airflow import DAG
from airflow.operators.python import PythonOperator, BranchPythonOperator
from airflow.operators.bash import BashOperator
from datetime import datetime
import random

default_args = {
    'owner': 'atam',
    'start_date': datetime(2024, 1, 1),
}

dag = DAG(
    'branching_example',
    default_args=default_args,
    schedule='@daily',
    catchup=False,
)

def choose_branch(**context):
    """
    Decide which branch to take based on condition
    """
    # Random choice for demo
    value = random.randint(1, 10)
    print(f"Random value: {value}")

    if value <= 5:
        return 'low_value_task'
    else:
        return 'high_value_task'

start_task = BashOperator(
    task_id='start',
    bash_command='echo "Starting branching workflow"',
    dag=dag,
)

branch_task = BranchPythonOperator(
    task_id='branch_decision',
    python_callable=choose_branch,
    dag=dag,
)

low_value_task = BashOperator(
    task_id='low_value_task',
    bash_command='echo "Processing low value"',
    dag=dag,
)

high_value_task = BashOperator(
    task_id='high_value_task',
    bash_command='echo "Processing high value"',
    dag=dag,
)

end_task = BashOperator(
    task_id='end',
    bash_command='echo "Workflow complete"',
    trigger_rule='none_failed_min_one_success',  # Run if any branch succeeds
    dag=dag,
)

# Set dependencies
start_task >> branch_task >> [low_value_task, high_value_task] >> end_task
```

---

## Understanding the Workflow

### DAG Lifecycle

```
1. DAG File Created
   ├─ Save .py file in dags_folder
   │
2. Scheduler Parses DAG
   ├─ Scans dags_folder every dag_dir_list_interval
   ├─ Imports Python file
   ├─ Extracts DAG objects
   │
3. DAG Appears in UI
   ├─ Navigate to http://localhost:8080
   ├─ See DAG in list
   ├─ Toggle ON to enable
   │
4. Scheduler Creates DagRun
   ├─ Based on schedule and start_date
   ├─ Creates TaskInstance for each task
   │
5. Tasks Execute
   ├─ Scheduler checks dependencies
   ├─ Queues ready tasks
   ├─ Executor runs tasks
   │
6. DagRun Completes
   ├─ All tasks finish (success/failed/skipped)
   ├─ DagRun marked complete
   └─ Wait for next scheduled run
```

### Task States

- **None**: Task not yet queued
- **Queued**: Waiting for executor slot
- **Running**: Currently executing
- **Success**: Completed successfully
- **Failed**: Execution failed
- **Skipped**: Skipped due to branching
- **Upstream Failed**: Parent task failed
- **Up for Retry**: Will retry after delay

---

## Common Commands

### DAG Management

```bash
# List all DAGs
airflow dags list

# Trigger a DAG manually
airflow dags trigger hello_world_dag

# Trigger with specific execution date
airflow dags trigger hello_world_dag --exec-date 2024-01-01

# Pause/Unpause DAG
airflow dags pause hello_world_dag
airflow dags unpause hello_world_dag

# Delete DAG from database (metadata only)
airflow dags delete hello_world_dag
```

### Task Management

```bash
# List tasks in a DAG
airflow tasks list hello_world_dag

# Test a task (without checking dependencies)
airflow tasks test hello_world_dag say_hello 2024-01-01

# Run a task (checks dependencies)
airflow tasks run hello_world_dag say_hello 2024-01-01

# Clear task state (allows re-run)
airflow tasks clear hello_world_dag --task-regex say_hello

# Get task state
airflow tasks state hello_world_dag say_hello 2024-01-01
```

### Database Management

```bash
# Initialize database (first time)
airflow db init

# Upgrade database schema (after Airflow upgrade)
airflow db upgrade

# Reset database (CAUTION: deletes all data)
airflow db reset

# Check database
airflow db check
```

### User Management

```bash
# Create user
airflow users create \
    --username john \
    --firstname John \
    --lastname Doe \
    --role Admin \
    --email john@example.com

# List users
airflow users list

# Delete user
airflow users delete john
```

### Connection Management

```bash
# Add connection
airflow connections add 'my_postgres' \
    --conn-type 'postgres' \
    --conn-host 'localhost' \
    --conn-login 'postgres' \
    --conn-password 'password' \
    --conn-port 5432 \
    --conn-schema 'airflow'

# List connections
airflow connections list

# Get connection
airflow connections get my_postgres

# Delete connection
airflow connections delete my_postgres

# Export connections
airflow connections export connections.json

# Import connections
airflow connections import connections.json
```

### Variable Management

```bash
# Set variable
airflow variables set my_key my_value

# Set from JSON
airflow variables set my_json_var '{"key": "value"}' --json

# Get variable
airflow variables get my_key

# List variables
airflow variables list

# Delete variable
airflow variables delete my_key

# Export variables
airflow variables export variables.json

# Import variables
airflow variables import variables.json
```

---

## Debugging Tips

### 1. Check DAG Import Errors

**In UI:**
- Navigate to Admin > Import Errors
- Shows Python import exceptions

**In CLI:**
```bash
# Check if DAG can be imported
python ~/airflow/dags/hello_world_dag.py

# List DAGs with import errors
airflow dags list-import-errors
```

### 2. View Task Logs

**In UI:**
- Click on task in Graph/Grid view
- Click "Log" button
- Real-time log streaming

**In CLI:**
```bash
# View logs for specific task run
cat ~/airflow/logs/hello_world_dag/say_hello/2024-01-01T00:00:00+00:00/1.log
```

### 3. Test Tasks Individually

```bash
# Test without dependencies or database updates
airflow tasks test hello_world_dag say_hello 2024-01-01

# Output shows exactly what happens
```

### 4. Check Configuration

```bash
# Show current configuration
airflow config list

# Get specific config value
airflow config get-value core dags_folder
```

### 5. Common Issues

**Issue: DAG not appearing**
```bash
# Check if DAG can be parsed
python ~/airflow/dags/your_dag.py

# Check scheduler logs
tail -f ~/airflow/logs/scheduler/*.log

# Force DAG bag refresh
# Stop scheduler, delete dag_bag, restart
```

**Issue: Tasks not running**
```bash
# Check scheduler is running
ps aux | grep "airflow scheduler"

# Check executor type
airflow config get-value core executor

# Check task dependencies
airflow tasks test dag_id task_id execution_date
```

**Issue: Database locked (SQLite)**
```bash
# SQLite doesn't support concurrent writes
# Solution: Use PostgreSQL or MySQL

# In airflow.cfg:
# sql_alchemy_conn = postgresql+psycopg2://user:pass@localhost/airflow
```

### 6. Enable Debug Logging

**Edit `airflow.cfg`:**
```ini
[logging]
logging_level = DEBUG
fab_logging_level = DEBUG
```

**In Python code:**
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

## Next Steps

### 1. Explore Example DAGs

Airflow comes with example DAGs:

```bash
# List example DAGs
airflow dags list | grep example

# View example DAG code
cat $AIRFLOW_HOME/lib/python3.x/site-packages/airflow/example_dags/example_bash_operator.py
```

### 2. Learn Advanced Concepts

- **XComs**: Task-to-task communication
- **Connections**: External system credentials
- **Variables**: Global configuration values
- **Pools**: Resource management
- **Sensors**: Waiting for conditions
- **Triggers**: Deferrable operations
- **Task Groups**: Organizing complex DAGs

### 3. Integrate External Systems

Explore provider packages:

```bash
# Install AWS provider
pip install apache-airflow-providers-amazon

# Install Google Cloud provider
pip install apache-airflow-providers-google

# List all available providers
pip search apache-airflow-providers
```

### 4. Production Setup

Key considerations:
- Use PostgreSQL or MySQL (not SQLite)
- Use CeleryExecutor or KubernetesExecutor
- Set up monitoring and alerting
- Configure authentication (LDAP, OAuth)
- Enable logging aggregation
- Set up high availability

### 5. Read the Documentation

- **Official Docs**: https://airflow.apache.org/docs/
- **Implementation Guide**: [./airflow-implementation-guide.md](./airflow-implementation-guide.md)
- **Scheduler Deep Dive**: [./scheduler-deep-dive.md](./scheduler-deep-dive.md)
- **Operators Guide**: [./operators-and-hooks-guide.md](./operators-and-hooks-guide.md)

### 6. Join the Community

- **GitHub**: https://github.com/apache/airflow
- **Slack**: apache-airflow.slack.com
- **Dev List**: dev@airflow.apache.org
- **Stack Overflow**: Tag `apache-airflow`

---

## Quick Reference Card

### Essential Commands

```bash
# Start services
airflow webserver --port 8080
airflow scheduler

# DAG operations
airflow dags list
airflow dags trigger <dag_id>
airflow dags pause <dag_id>

# Task operations
airflow tasks list <dag_id>
airflow tasks test <dag_id> <task_id> <execution_date>

# Database
airflow db init
airflow db upgrade
airflow db reset

# Users
airflow users create --username admin --role Admin
airflow users list
```

### DAG Template

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

default_args = {
    'owner': 'your_name',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'dag_id',
    default_args=default_args,
    start_date=datetime(2024, 1, 1),
    schedule='@daily',
    catchup=False,
)

def my_function(**context):
    print("Hello!")

task = PythonOperator(
    task_id='task_id',
    python_callable=my_function,
    dag=dag,
)
```

### Schedule Presets

```python
None           # Don't schedule, manual only
'@once'        # Run once
'@hourly'      # 0 * * * *
'@daily'       # 0 0 * * *
'@weekly'      # 0 0 * * 0
'@monthly'     # 0 0 1 * *
'@yearly'      # 0 0 1 1 *

# Custom cron
'30 2 * * *'   # 2:30 AM daily
'0 */4 * * *'  # Every 4 hours

# Timedelta
timedelta(hours=1)
timedelta(days=1)
```

---

## Troubleshooting Checklist

- [ ] Is the scheduler running? (`ps aux | grep scheduler`)
- [ ] Is the DAG paused? (Check toggle in UI)
- [ ] Does the DAG have import errors? (Admin > Import Errors)
- [ ] Is the start_date in the past?
- [ ] Is catchup enabled when you don't want it?
- [ ] Are dependencies satisfied?
- [ ] Are pool slots available?
- [ ] Check task logs in UI or filesystem
- [ ] Try `airflow tasks test` to isolate the issue
- [ ] Check `airflow.cfg` settings

---

**Document Version**: 1.0
**Last Updated**: 2026-02-20
**Maintained By**: Atam Agrawal
