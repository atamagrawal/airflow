# Apache Airflow Implementation Guide

> Comprehensive guide to understanding Apache Airflow's codebase and architecture
>
> GitHub: https://github.com/apache/airflow

## Table of Contents

1. [Overview](#overview)
2. [Directory Structure](#directory-structure)
3. [Core Concepts](#core-concepts)
4. [Key Components Deep Dive](#key-components-deep-dive)
5. [How It All Works Together](#how-it-all-works-together)
6. [Code Walkthroughs](#code-walkthroughs)

---

## Overview

Apache Airflow is a platform to programmatically author, schedule, and monitor workflows. It represents workflows as Directed Acyclic Graphs (DAGs) of tasks.

### Key Design Principles

- **Dynamic**: Workflows are defined in Python code, allowing dynamic pipeline generation
- **Extensible**: Easily define custom operators, executors, and hooks
- **Elegant**: Workflows are lean, explicit, and use Jinja templating
- **Scalable**: Modular architecture with message queue for orchestration

---

## Directory Structure

```
airflow/
├── airflow-core/              # Core Airflow implementation
│   └── src/airflow/
│       ├── models/            # ORM models (DAG, TaskInstance, etc.)
│       ├── operators/         # Built-in operators
│       ├── executors/         # Task execution strategies
│       ├── hooks/             # External system connectors
│       ├── cli/               # Command-line interface
│       ├── api/               # REST API
│       ├── www/               # Web UI
│       ├── sensors/           # Waiting/polling operators
│       ├── configuration.py   # Configuration management
│       ├── settings.py        # Global settings
│       └── __init__.py        # Package initialization
│
├── providers/                 # Provider packages (AWS, GCP, etc.)
├── task-sdk/                  # Task SDK for task definitions
├── go-sdk/                    # Go SDK implementation
├── docs/                      # Documentation
├── chart/                     # Kubernetes Helm charts
└── scripts/                   # CI/CD and utility scripts
```

### Core Source Code Location

Primary code: `airflow-core/src/airflow/`

---

## Core Concepts

### 1. DAG (Directed Acyclic Graph)

A DAG defines a workflow - a collection of tasks with dependencies.

**Key Files:**
- `airflow-core/src/airflow/models/dag.py` - DAG model
- `airflow-core/src/airflow/sdk/dag.py` - DAG SDK

**Core Attributes:**
- `dag_id`: Unique identifier
- `schedule`: When to run (cron, timedelta, etc.)
- `start_date`: When DAG becomes active
- `catchup`: Whether to backfill past runs
- `tags`: Metadata for organization

### 2. Operator

An operator defines a single task - a unit of work.

**Types:**
- **Action Operators**: Execute something (BashOperator, PythonOperator)
- **Transfer Operators**: Move data between systems
- **Sensors**: Wait for conditions to be met

**Key Files:**
- `airflow-core/src/airflow/models/baseoperator.py` - Base operator class
- `airflow-core/src/airflow/operators/` - Built-in operators

### 3. TaskInstance

A TaskInstance represents a specific run of a task.

**Key File:** `airflow-core/src/airflow/models/taskinstance.py`

**States:**
- `queued`: Waiting to execute
- `running`: Currently executing
- `success`: Completed successfully
- `failed`: Execution failed
- `skipped`: Conditional execution skipped
- `upstream_failed`: Upstream dependency failed

### 4. Executor

Executors determine how and where tasks run.

**Key File:** `airflow-core/src/airflow/executors/`

**Built-in Executors:**
- `SequentialExecutor`: Single process (development only)
- `LocalExecutor`: Multiple processes on one machine
- `CeleryExecutor`: Distributed execution via Celery
- `KubernetesExecutor`: Each task in a Kubernetes pod

### 5. Scheduler

The scheduler monitors DAGs and triggers task execution.

**Key Files:**
- `airflow-core/src/airflow/jobs/scheduler_job.py`
- `airflow-core/src/airflow/jobs/scheduler_job_runner.py`

**Responsibilities:**
- Parse DAG files
- Create DagRuns
- Schedule TaskInstances
- Monitor execution state

### 6. Hooks

Hooks provide interfaces to external systems.

**Key File:** `airflow-core/src/airflow/hooks/`

**Examples:**
- `base.py`: Base hook class
- Connection management
- Authentication handling

---

## Key Components Deep Dive

### Component 1: Initialization Flow

**File:** `airflow-core/src/airflow/__init__.py`

```python
# Key initialization steps:

1. Configuration loading (line 64)
   from airflow import configuration, settings

2. Settings initialization (line 81)
   if not os.environ.get("_AIRFLOW__AS_LIBRARY", None):
       settings.initialize()

3. Lazy imports for main objects (line 84-96)
   - DAG
   - Asset
   - XComArg

4. Provider loading (line 130-136)
   if not settings.LAZY_LOAD_PROVIDERS:
       manager = ProvidersManager()
       manager.initialize_providers_list()
```

**What Happens:**
1. Configuration is loaded from `airflow.cfg`
2. Database connections are established
3. Logging is configured
4. Providers and plugins are discovered and loaded

### Component 2: DAG Definition

**Example DAG Structure:**

```python
from airflow import DAG
from airflow.operators.python import PythonOperator
from datetime import datetime, timedelta

# Define default arguments
default_args = {
    'owner': 'airflow',
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Create DAG
with DAG(
    dag_id='example_dag',
    default_args=default_args,
    schedule='@daily',
    start_date=datetime(2024, 1, 1),
    catchup=False,
) as dag:

    # Define tasks
    task1 = PythonOperator(
        task_id='task_1',
        python_callable=my_function,
    )

    task2 = PythonOperator(
        task_id='task_2',
        python_callable=my_other_function,
    )

    # Set dependencies
    task1 >> task2  # task2 runs after task1
```

### Component 3: Models and ORM

**Key Files in `airflow-core/src/airflow/models/`:**

1. **`dag.py`** - DAG model
   - Represents a workflow definition
   - Stores DAG metadata in database

2. **`dagrun.py`** - DagRun model
   - Represents a specific execution of a DAG
   - Tracks execution state and timing

3. **`taskinstance.py`** - TaskInstance model
   - Represents a specific execution of a task
   - Tracks task state, retries, logs

4. **`dagbag.py`** - DagBag model
   - Container for DAG parsing
   - Handles DAG file discovery and loading

5. **`connection.py`** - Connection model
   - Stores connection credentials
   - Used by Hooks to connect to external systems

**Database Schema:**
- Uses SQLAlchemy ORM
- Supports PostgreSQL, MySQL, SQLite
- Migrations managed via Alembic

### Component 4: Scheduler Architecture

**Main Scheduler Loop:**

1. **Parse DAGs** (`airflow-core/src/airflow/models/dagbag.py`)
   - Scan DAG directory
   - Import Python files
   - Extract DAG definitions

2. **Create DagRuns** (`airflow-core/src/airflow/models/dagrun.py`)
   - Based on schedule interval
   - Check if run should be created
   - Create database entry

3. **Schedule Tasks** (`airflow-core/src/airflow/jobs/scheduler_job_runner.py`)
   - Find tasks ready to run
   - Check dependencies
   - Queue tasks for execution

4. **Execute Tasks** (via Executor)
   - Send to executor
   - Monitor execution
   - Update state in database

### Component 5: Task Execution Flow

**Execution Path:**

```
1. Scheduler → Queues TaskInstance
   File: airflow-core/src/airflow/jobs/scheduler_job_runner.py

2. Executor → Picks up task
   File: airflow-core/src/airflow/executors/base_executor.py

3. Task Runner → Executes task in subprocess
   File: airflow-core/src/airflow/task/task_runner/

4. Operator.execute() → Runs task logic
   File: airflow-core/src/airflow/models/baseoperator.py

5. State Update → Updates database
   File: airflow-core/src/airflow/models/taskinstance.py
```

### Component 6: CLI Interface

**Key File:** `airflow-core/src/airflow/cli/cli_parser.py`

**Common Commands:**
- `airflow dags list` - List all DAGs
- `airflow tasks test` - Test a specific task
- `airflow dags trigger` - Manually trigger a DAG
- `airflow scheduler` - Start the scheduler
- `airflow webserver` - Start the web UI

**Command Structure:**
```
airflow [group] [command] [options]

Examples:
- airflow dags list
- airflow tasks run dag_id task_id execution_date
- airflow db init  # Initialize database
```

### Component 7: Web UI

**Key Directory:** `airflow-core/src/airflow/www/`

**Technologies:**
- **Backend**: Flask (Python web framework)
- **Frontend**: React (JavaScript library)
- **Authentication**: Flask-AppBuilder

**Main Views:**
- DAG view: Visual representation of workflow
- Grid view: Task execution history
- Graph view: Dependencies visualization
- Gantt view: Task duration timeline

---

## How It All Works Together

### Workflow Execution Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│ 1. DAG File (Python)                                        │
│    - Define workflow structure                              │
│    - Set schedules and dependencies                         │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Scheduler                                                │
│    - Parse DAG files                                        │
│    - Create DagRuns based on schedule                       │
│    - Determine which tasks are ready                        │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Database                                                 │
│    - Store DAG metadata                                     │
│    - Track DagRun and TaskInstance state                    │
│    - Queue tasks for execution                              │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Executor                                                 │
│    - Pick up queued tasks                                   │
│    - Distribute to workers                                  │
│    - Monitor execution                                      │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. Worker                                                   │
│    - Execute task code                                      │
│    - Run operator.execute()                                 │
│    - Update state in database                               │
└────────────────┬────────────────────────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Result                                                   │
│    - Task completes (success/failure)                       │
│    - Scheduler moves to next task                           │
│    - Repeat until DAG completes                             │
└─────────────────────────────────────────────────────────────┘
```

### Key Interactions

1. **DAG Parsing**
   - Scheduler reads Python files from DAG folder
   - Imports files and extracts DAG objects
   - Stores DAG metadata in `dag` table

2. **Task Scheduling**
   - Scheduler checks schedule interval
   - Creates DagRun in database
   - Evaluates task dependencies
   - Queues ready tasks

3. **Task Execution**
   - Executor picks task from queue
   - Spawns worker process/pod
   - Worker executes operator code
   - Updates TaskInstance state

4. **State Management**
   - All state stored in database
   - Scheduler continuously monitors
   - UI queries database for display

---

## Code Walkthroughs

### Walkthrough 1: How a DAG is Parsed

**Start:** Scheduler starts
**Goal:** Load DAG definitions from Python files

**Code Flow:**

```
1. Scheduler initialization
   File: airflow-core/src/airflow/jobs/scheduler_job_runner.py
   Method: SchedulerJobRunner._execute()

2. Create DagBag
   File: airflow-core/src/airflow/models/dagbag.py
   Method: DagBag.__init__()
   - Scans DAG directory
   - Gets list of Python files

3. Process each file
   File: airflow-core/src/airflow/models/dagbag.py
   Method: DagBag.process_file()
   - Imports Python file
   - Executes code
   - Extracts DAG objects

4. Store in database
   File: airflow-core/src/airflow/models/dag.py
   Method: DAG.sync_to_db()
   - Serializes DAG to JSON
   - Stores in serialized_dag table
```

**Key Code Locations:**
- DAG file scanning: `airflow-core/src/airflow/models/dagbag.py:DagBag.collect_dags()`
- File processing: `airflow-core/src/airflow/models/dagbag.py:DagBag.process_file()`
- Database sync: `airflow-core/src/airflow/models/dag.py:DAG.sync_to_db()`

### Walkthrough 2: How a Task is Executed

**Start:** Task is queued
**Goal:** Execute task code and update state

**Code Flow:**

```
1. Executor picks up task
   File: airflow-core/src/airflow/executors/base_executor.py
   Method: BaseExecutor.heartbeat()
   - Queries database for queued tasks
   - Sends to executor queue

2. Spawn task process
   File: airflow-core/src/airflow/executors/local_executor.py
   Method: LocalExecutor._execute_task()
   - Creates subprocess
   - Runs airflow tasks run command

3. Task runner setup
   File: airflow-core/src/airflow/task/task_runner/standard_task_runner.py
   Method: StandardTaskRunner.start()
   - Sets up logging
   - Prepares execution context

4. Execute operator
   File: airflow-core/src/airflow/models/taskinstance.py
   Method: TaskInstance._execute_task()
   - Calls operator.execute()
   - Handles retries and errors

5. Update state
   File: airflow-core/src/airflow/models/taskinstance.py
   Method: TaskInstance.set_state()
   - Updates database
   - Logs completion
```

**Key Code Locations:**
- Task execution entry: `airflow-core/src/airflow/models/taskinstance.py:TaskInstance._execute_task()`
- Operator execution: `airflow-core/src/airflow/models/baseoperator.py:BaseOperator.execute()`
- State management: `airflow-core/src/airflow/models/taskinstance.py:TaskInstance.set_state()`

### Walkthrough 3: How Dependencies are Resolved

**Start:** DAG run is created
**Goal:** Determine which tasks can run

**Code Flow:**

```
1. DAG run creation
   File: airflow-core/src/airflow/models/dagrun.py
   Method: DAG.create_dagrun()
   - Creates DagRun entry
   - Creates TaskInstance entries for all tasks

2. Dependency checking
   File: airflow-core/src/airflow/ti_deps/deps/
   - Multiple dependency classes check conditions
   - TriggerRuleDep: Check trigger rule
   - DagrunRunningDep: DAG is running
   - PrevDagrunDep: Previous runs complete

3. Task readiness evaluation
   File: airflow-core/src/airflow/models/taskinstance.py
   Method: TaskInstance.are_dependencies_met()
   - Checks all dependency criteria
   - Returns True if task can run

4. Scheduler queues task
   File: airflow-core/src/airflow/jobs/scheduler_job_runner.py
   Method: SchedulerJobRunner._schedule_task_instances()
   - Queries ready tasks
   - Sets state to QUEUED
```

**Key Code Locations:**
- Dependency framework: `airflow-core/src/airflow/ti_deps/`
- Dependency evaluation: `airflow-core/src/airflow/models/taskinstance.py:TaskInstance.are_dependencies_met()`
- Task scheduling: `airflow-core/src/airflow/jobs/scheduler_job_runner.py`

### Walkthrough 4: How Configuration Works

**Start:** Airflow process starts
**Goal:** Load and validate configuration

**Code Flow:**

```
1. Import configuration module
   File: airflow-core/src/airflow/__init__.py
   Line: 64
   from airflow import configuration

2. Configuration initialization
   File: airflow-core/src/airflow/configuration.py
   - Reads airflow.cfg file
   - Reads environment variables
   - Applies defaults

3. Settings initialization
   File: airflow-core/src/airflow/settings.py
   Method: initialize()
   - Sets up database connection
   - Configures logging
   - Initializes stats

4. Access configuration
   File: airflow-core/src/airflow/configuration.py
   Method: conf.get()
   - Retrieve configuration values
   - Type conversion
   - Validation
```

**Configuration Precedence:**
1. Environment variables (highest)
2. airflow.cfg file
3. Default values (lowest)

**Key Code Locations:**
- Configuration class: `airflow-core/src/airflow/configuration.py:AirflowConfigParser`
- Settings initialization: `airflow-core/src/airflow/settings.py:initialize()`
- Default config: `airflow-core/src/airflow/config_templates/default_airflow.cfg`

### Walkthrough 5: How Operators Work

**Start:** Custom operator creation
**Goal:** Understand operator lifecycle

**Code Flow:**

```
1. Define operator class
   Inherit from: BaseOperator
   File: airflow-core/src/airflow/models/baseoperator.py

   class MyOperator(BaseOperator):
       def __init__(self, my_param, **kwargs):
           super().__init__(**kwargs)
           self.my_param = my_param

       def execute(self, context):
           # Your task logic here
           pass

2. Operator instantiation (in DAG file)
   - Called when DAG is parsed
   - Stores operator in DAG
   - Creates task dependencies

3. Operator execution (at runtime)
   File: airflow-core/src/airflow/models/taskinstance.py
   Method: TaskInstance._execute_task()
   - Calls operator.pre_execute()
   - Calls operator.execute()
   - Calls operator.post_execute()

4. Result handling
   - Return value stored as XCom
   - State updated to SUCCESS
```

**Base Operator Key Methods:**
- `__init__()`: Initialize operator
- `execute()`: Main task logic (must implement)
- `pre_execute()`: Setup before execution
- `post_execute()`: Cleanup after execution

**Key Code Locations:**
- Base operator: `airflow-core/src/airflow/models/baseoperator.py:BaseOperator`
- Built-in operators: `airflow-core/src/airflow/operators/`
- Operator execution: `airflow-core/src/airflow/models/taskinstance.py:TaskInstance._execute_task()`

### Walkthrough 6: How XComs Work (Cross-Communication)

**What are XComs?**
XComs allow tasks to exchange small amounts of data.

**Code Flow:**

```
1. Push data to XCom
   File: airflow-core/src/airflow/models/taskinstance.py
   Method: TaskInstance.xcom_push()

   # In operator execute method:
   context['task_instance'].xcom_push(key='my_key', value='my_value')

   # Or return value (auto-pushed as 'return_value'):
   return 'my_value'

2. Store in database
   File: airflow-core/src/airflow/models/xcom.py
   Table: xcom
   - Serializes value (JSON/pickle)
   - Stores with dag_id, task_id, run_id, key

3. Pull data from XCom
   Method: TaskInstance.xcom_pull()

   # In downstream operator:
   value = context['task_instance'].xcom_pull(
       task_ids='upstream_task',
       key='my_key'
   )

4. Retrieve from database
   File: airflow-core/src/airflow/models/xcom.py
   Method: XCom.get_value()
   - Queries database
   - Deserializes value
   - Returns to caller
```

**Key Code Locations:**
- XCom model: `airflow-core/src/airflow/models/xcom.py`
- Push/Pull methods: `airflow-core/src/airflow/models/taskinstance.py`

---

## Important Directories Reference

### Core Implementation
- **Models**: `airflow-core/src/airflow/models/`
  - All database models and ORM classes

- **Operators**: `airflow-core/src/airflow/operators/`
  - Built-in operators (Bash, Python, etc.)

- **Executors**: `airflow-core/src/airflow/executors/`
  - Execution strategies

- **CLI**: `airflow-core/src/airflow/cli/`
  - Command-line interface

- **API**: `airflow-core/src/airflow/api/`
  - REST API endpoints

- **Web UI**: `airflow-core/src/airflow/www/`
  - Flask web application

### Extensions
- **Providers**: `providers/`
  - Cloud and service integrations (AWS, GCP, Azure, etc.)

- **Hooks**: `airflow-core/src/airflow/hooks/`
  - Connection interfaces

- **Sensors**: `airflow-core/src/airflow/sensors/`
  - Waiting/polling operators

---

## Additional Resources

### Official Documentation
- Main docs: https://airflow.apache.org/docs/
- GitHub repo: https://github.com/apache/airflow
- API reference: https://airflow.apache.org/docs/apache-airflow/stable/python-api-ref.html

### Key GitHub Paths
- Core source: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow
- Models: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow/models
- Operators: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow/operators
- Providers: https://github.com/apache/airflow/tree/main/providers

### Community
- Dev mailing list: dev@airflow.apache.org
- Slack: apache-airflow.slack.com
- Contributing guide: CONTRIBUTING.rst

---

## Next Steps

1. **Start Simple**: Run example DAGs from `airflow-core/src/airflow/example_dags/`
2. **Read Models**: Understand core models in `models/dag.py`, `models/taskinstance.py`
3. **Create Custom Operator**: Extend BaseOperator for your use case
4. **Explore Providers**: Check providers for integrations you need
5. **Debug**: Use `airflow tasks test` to run tasks in isolation

---

**Document Version**: 1.0
**Last Updated**: 2026-02-20
**Maintained By**: Atam Agrawal
