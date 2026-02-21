# Airflow Scheduler Deep Dive

> Comprehensive guide to understanding how the Airflow Scheduler works
>
> GitHub: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow/jobs

## Table of Contents

1. [Overview](#overview)
2. [Scheduler Architecture](#scheduler-architecture)
3. [Main Scheduler Loop](#main-scheduler-loop)
4. [Code Walkthrough](#code-walkthrough)
5. [Key Algorithms](#key-algorithms)
6. [Performance Considerations](#performance-considerations)

---

## Overview

The Scheduler is the heart of Airflow. It's responsible for:

1. **Parsing DAG files** - Discovering and loading workflow definitions
2. **Creating DagRuns** - Instantiating workflow executions based on schedules
3. **Scheduling Tasks** - Determining which tasks are ready to run
4. **Monitoring Execution** - Tracking task state and handling failures
5. **Managing Dependencies** - Ensuring tasks run in the correct order

**Key Files:**
- `airflow-core/src/airflow/jobs/scheduler_job.py` - Scheduler job definition
- `airflow-core/src/airflow/jobs/scheduler_job_runner.py` - Main scheduler loop
- `airflow-core/src/airflow/models/dagbag.py` - DAG parsing logic

---

## Scheduler Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Scheduler Process                          │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Main Loop (scheduler_job_runner.py)                     │  │
│  │                                                           │  │
│  │  1. Parse DAG Files ──────────────────────┐              │  │
│  │                                            │              │  │
│  │  2. Create/Update DagRuns ◄────────────────┘             │  │
│  │                                            │              │  │
│  │  3. Schedule Tasks ◄────────────────────────┘            │  │
│  │                                            │              │  │
│  │  4. Send to Executor ◄──────────────────────┘            │  │
│  │                                            │              │  │
│  │  5. Update Task State ◄──────────────────────┘           │  │
│  │                                            │              │  │
│  │  6. Sleep/Heartbeat ◄───────────────────────┘            │  │
│  │                                                           │  │
│  │  └─────► Loop back to 1                                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐     │
│  │ DAG Parser   │    │ DagRun Mgr   │    │ Task Queue   │     │
│  │ Processor    │    │              │    │ Manager      │     │
│  └──────────────┘    └──────────────┘    └──────────────┘     │
└─────────────────────────────────────────────────────────────────┘
           │                    │                     │
           ▼                    ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         Database                                │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌───────────┐  │
│  │   dag    │  │ dag_run  │  │ task_instance│  │  dag_code │  │
│  └──────────┘  └──────────┘  └──────────────┘  └───────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Main Scheduler Loop

### Phase 1: DAG File Parsing

**Location:** `airflow-core/src/airflow/models/dagbag.py`

```python
# Simplified code flow:

class DagBag:
    def __init__(self, dag_folder=None):
        """
        Scan DAG folder and parse all Python files
        """
        self.dags = {}
        self.import_errors = {}

        # Get all .py files from dag_folder
        dag_files = self._list_py_files(dag_folder)

        # Process each file
        for file_path in dag_files:
            self.process_file(file_path)

    def process_file(self, file_path):
        """
        Import a Python file and extract DAG objects
        """
        try:
            # Import the module
            module = importlib.import_module(file_path)

            # Find all DAG objects in the module
            for obj_name, obj in inspect.getmembers(module):
                if isinstance(obj, DAG):
                    self.dags[obj.dag_id] = obj

        except Exception as e:
            self.import_errors[file_path] = str(e)
```

**Key Points:**
- Scheduler scans `dags_folder` (configured in `airflow.cfg`)
- Imports each `.py` file as a Python module
- Extracts all `DAG` objects found in module globals
- Errors are captured and displayed in UI
- Runs periodically (controlled by `dag_dir_list_interval`)

**GitHub:** https://github.com/apache/airflow/blob/main/airflow-core/src/airflow/models/dagbag.py

### Phase 2: DagRun Creation

**Location:** `airflow-core/src/airflow/models/dag.py`

```python
# Simplified code flow:

class DAG:
    def create_dagrun(
        self,
        run_id: str,
        execution_date: datetime,
        state: DagRunState = DagRunState.RUNNING,
        ...
    ):
        """
        Create a DagRun - an instance of a DAG execution
        """
        # Create DagRun entry
        dagrun = DagRun(
            dag_id=self.dag_id,
            run_id=run_id,
            execution_date=execution_date,
            start_date=timezone.utcnow(),
            state=state,
        )

        # Add to database
        session.add(dagrun)
        session.flush()

        # Create TaskInstance entries for all tasks
        for task in self.tasks:
            ti = TaskInstance(task=task, run_id=run_id)
            session.add(ti)

        session.commit()
        return dagrun
```

**When are DagRuns created?**

```python
# In scheduler_job_runner.py

def _create_dag_runs(self, dag, session):
    """
    Check if we need to create new DagRuns based on schedule
    """
    # Get last DagRun
    last_dagrun = dag.get_last_dagrun(session=session)

    # Calculate next execution date based on schedule
    if dag.timetable:
        next_run = dag.timetable.next_dagrun_info(
            last_automated_dagrun=last_dagrun,
            restriction=TimeRestriction(...),
        )

        if next_run and next_run.logical_date <= timezone.utcnow():
            # Time to create a new DagRun
            dag.create_dagrun(
                run_id=f"scheduled__{next_run.logical_date}",
                execution_date=next_run.logical_date,
                state=DagRunState.QUEUED,
                session=session,
            )
```

**Key Points:**
- Scheduler checks each DAG's schedule (cron, timedelta, etc.)
- Creates DagRun when schedule indicates it's time
- DagRun includes all TaskInstances (in pending state)
- Respects `start_date` and `end_date` boundaries
- Handles `catchup` configuration

**GitHub:** https://github.com/apache/airflow/blob/main/airflow-core/src/airflow/models/dag.py

### Phase 3: Task Scheduling

**Location:** `airflow-core/src/airflow/jobs/scheduler_job_runner.py`

```python
# Simplified code flow:

class SchedulerJobRunner:
    def _schedule_task_instances(self, dag, dagrun, session):
        """
        Find tasks that are ready to run and queue them
        """
        # Get all TaskInstances for this DagRun
        task_instances = dagrun.get_task_instances(session=session)

        for ti in task_instances:
            # Skip if already queued/running/success
            if ti.state in [State.QUEUED, State.RUNNING, State.SUCCESS]:
                continue

            # Check if dependencies are met
            if self._are_dependencies_met(ti, session):
                # Queue the task
                ti.state = State.QUEUED
                ti.queued_dttm = timezone.utcnow()

                # Send to executor
                self.executor.queue_task_instance(ti)

        session.commit()

    def _are_dependencies_met(self, ti, session):
        """
        Check if task can run based on dependencies
        """
        # Check upstream tasks
        for upstream_ti in ti.get_upstream_task_instances(session):
            if upstream_ti.state != State.SUCCESS:
                # Check trigger rule
                if not ti.are_dependencies_met_for_trigger_rule():
                    return False

        # Check other dependencies (pool slots, concurrency, etc.)
        dep_context = DepContext()
        if not ti.are_dependencies_met(dep_context=dep_context, session=session):
            return False

        return True
```

**Dependency Types:**

1. **Upstream Dependencies** - Parent tasks must complete
2. **Trigger Rules** - How to handle upstream states
   - `all_success`: All parents must succeed (default)
   - `all_failed`: All parents must fail
   - `one_success`: At least one parent succeeds
   - `all_done`: All parents complete (any state)
3. **Concurrency Limits** - Max tasks per DAG
4. **Pool Slots** - Resource management
5. **Priority** - Task priority weight

**GitHub:** https://github.com/apache/airflow/blob/main/airflow-core/src/airflow/jobs/scheduler_job_runner.py

### Phase 4: Task Execution

**Location:** Integration with Executor

```python
# In scheduler_job_runner.py

def _execute(self, session):
    """
    Main scheduler loop
    """
    while True:
        # Get tasks from executor that have completed
        completed_tasks = self.executor.heartbeat()

        # Update their state in database
        for ti, state in completed_tasks:
            ti.set_state(state, session=session)

        # Schedule new tasks
        self._schedule_task_instances(...)

        # Send queued tasks to executor
        self.executor.sync()

        # Sleep if nothing to do
        time.sleep(self.processor_poll_interval)
```

**Executor Interface:**

```python
class BaseExecutor:
    def queue_task_instance(self, task_instance):
        """Queue a task for execution"""
        self.queued_tasks[task_instance.key] = task_instance

    def sync(self):
        """Send queued tasks to workers"""
        for key, ti in self.queued_tasks.items():
            self._execute_task(ti)

    def heartbeat(self):
        """Check for completed tasks"""
        completed = []
        for key, ti in self.running_tasks.items():
            if self._is_complete(ti):
                completed.append((ti, self._get_final_state(ti)))
        return completed
```

---

## Code Walkthrough

### Full Scheduler Execution Flow

**Start:** `airflow scheduler` command
**File:** `airflow-core/src/airflow/cli/commands/scheduler_command.py`

```
1. CLI entry point
   ├─ scheduler_command.py:scheduler()
   │
2. Create SchedulerJob
   ├─ jobs/scheduler_job.py:SchedulerJob.__init__()
   │
3. Start job execution
   ├─ jobs/base_job.py:Job.run()
   ├─ jobs/scheduler_job_runner.py:SchedulerJobRunner._execute()
   │
4. Main loop begins
   │
   ├─ LOOP START ─────────────────────────────────────────┐
   │                                                       │
5. Parse DAG files                                        │
   ├─ models/dagbag.py:DagBag.collect_dags()            │
   │  └─ For each .py file:                              │
   │     └─ DagBag.process_file()                        │
   │        └─ Import module                             │
   │        └─ Extract DAG objects                       │
   │                                                       │
6. Sync DAGs to database                                  │
   ├─ models/dag.py:DAG.sync_to_db()                     │
   │  └─ Serialize DAG                                    │
   │  └─ Store in serialized_dag table                   │
   │                                                       │
7. Create DagRuns                                         │
   ├─ For each DAG:                                       │
   │  └─ Check schedule                                   │
   │  └─ If needed: DAG.create_dagrun()                  │
   │     └─ Create DagRun entry                          │
   │     └─ Create TaskInstance entries                  │
   │                                                       │
8. Schedule tasks                                         │
   ├─ For each DagRun:                                    │
   │  └─ Get TaskInstances                               │
   │  └─ For each TaskInstance:                          │
   │     └─ Check state                                   │
   │     └─ Check dependencies                           │
   │     └─ If ready: Set to QUEUED                      │
   │     └─ Send to executor                             │
   │                                                       │
9. Sync with executor                                     │
   ├─ executor.sync()                                     │
   │  └─ Send queued tasks to workers                    │
   │                                                       │
10. Check completed tasks                                 │
   ├─ executor.heartbeat()                               │
   │  └─ Get completed tasks                             │
   │  └─ Update TaskInstance state                       │
   │                                                       │
11. Health check & sleep                                  │
   ├─ Update scheduler heartbeat                         │
   ├─ Sleep for processor_poll_interval                  │
   │                                                       │
   └─ LOOP BACK TO 5 ────────────────────────────────────┘
```

### Key Methods Deep Dive

#### Method 1: DAG Parsing

**File:** `airflow-core/src/airflow/models/dagbag.py`

```python
def process_file(self, filepath, only_if_updated=True, safe_mode=True):
    """
    Process a Python file and extract DAG objects

    Args:
        filepath: Path to Python file
        only_if_updated: Skip if file hasn't changed
        safe_mode: Enable safety checks
    """
    # Check if file was modified
    if only_if_updated and not self._is_file_modified(filepath):
        return

    # Safety check - look for blacklisted imports
    if safe_mode and self._has_blacklisted_imports(filepath):
        self.import_errors[filepath] = "Contains blacklisted imports"
        return

    # Create a module name from file path
    mod_name = self._get_module_name(filepath)

    try:
        # Import the module
        loader = importlib.machinery.SourceFileLoader(mod_name, filepath)
        spec = importlib.util.spec_from_loader(mod_name, loader)
        new_module = importlib.util.module_from_spec(spec)

        # Execute module code
        loader.exec_module(new_module)

        # Extract DAG objects
        for obj_name in dir(new_module):
            if not obj_name.startswith('_'):  # Skip private
                obj = getattr(new_module, obj_name)
                if isinstance(obj, DAG):
                    # Add to DagBag
                    self.bag_dag(dag=obj, root_dag=obj)

    except Exception as e:
        # Capture import error
        self.import_errors[filepath] = str(e)
        self.log.exception(f"Failed to import {filepath}")
```

**Key Points:**
- Only re-parses files that have been modified
- Performs safety checks on imports
- Captures and logs errors without crashing
- Extracts all DAG instances from module globals

#### Method 2: Task Dependency Resolution

**File:** `airflow-core/src/airflow/models/taskinstance.py`

```python
def are_dependencies_met(self, dep_context=None, session=None):
    """
    Check if all dependencies are met for this task instance

    Returns:
        bool: True if can run, False otherwise
    """
    if not dep_context:
        dep_context = DepContext()

    # Get all dependency classes to check
    dependencies = self.get_dependency_classes(dep_context)

    # Check each dependency
    for dep_cls in dependencies:
        dep = dep_cls()

        # Each dep class has is_met() method
        if not dep.is_met(ti=self, session=session):
            # Log why dependency not met
            reason = dep.get_failure_reason(ti=self, session=session)
            self.log.info(f"Dependency not met: {dep.__class__.__name__}: {reason}")
            return False

    return True

def get_dependency_classes(self, dep_context):
    """
    Get list of dependency classes to check
    """
    deps = [
        # Core dependencies
        DagrunRunningDep,        # DagRun is running
        DagTISlotsAvailableDep,  # Concurrency limit
        ExecDateAfterStartDateDep, # After DAG start_date
        NotInRetryPeriodDep,     # Not in retry delay
        NotPreviouslySkippedDep,  # Not previously skipped
        PrevDagrunDep,           # Previous DagRun complete (if depends_on_past)
        TaskConcurrencyDep,      # Task concurrency limit
        TriggerRuleDep,          # Trigger rule satisfied
        ValidStateDep,           # In valid state for running
        PoolSlotsAvailableDep,   # Pool has available slots
    ]

    # Filter based on context
    if dep_context.ignore_in_retry_period:
        deps.remove(NotInRetryPeriodDep)

    return deps
```

**Dependency Classes Location:** `airflow-core/src/airflow/ti_deps/deps/`

Example dependency:

```python
# File: airflow-core/src/airflow/ti_deps/deps/trigger_rule_dep.py

class TriggerRuleDep(BaseTIDep):
    """
    Check if trigger rule is satisfied
    """

    def is_met(self, ti, session=None):
        """
        Check if upstream tasks satisfy trigger rule
        """
        trigger_rule = ti.task.trigger_rule
        upstream_states = self._get_upstream_states(ti, session)

        if trigger_rule == TriggerRule.ALL_SUCCESS:
            return all(state == State.SUCCESS for state in upstream_states)

        elif trigger_rule == TriggerRule.ALL_FAILED:
            return all(state == State.FAILED for state in upstream_states)

        elif trigger_rule == TriggerRule.ONE_SUCCESS:
            return any(state == State.SUCCESS for state in upstream_states)

        elif trigger_rule == TriggerRule.ALL_DONE:
            return all(state in State.finished for state in upstream_states)

        # ... other trigger rules

        return False
```

---

## Key Algorithms

### Algorithm 1: Next Run Calculation

**Purpose:** Determine when to create next DagRun

```python
def next_dagrun_info(
    self,
    last_automated_dagrun: DagRun | None,
    restriction: TimeRestriction,
) -> DagRunInfo | None:
    """
    Calculate next DagRun based on schedule

    Example for @daily schedule:
    - Last run: 2024-01-01 00:00:00
    - Next run: 2024-01-02 00:00:00
    """
    if last_automated_dagrun is None:
        # First run - use start_date
        next_run_date = restriction.earliest
    else:
        # Calculate next run based on timetable
        next_run_date = self.timetable.next_dagrun_after(
            last_automated_dagrun.execution_date,
            restriction,
        )

    # Check if next run is in the future
    if next_run_date > timezone.utcnow():
        return None  # Not time yet

    # Check if past end_date
    if restriction.latest and next_run_date > restriction.latest:
        return None  # Past end date

    return DagRunInfo(
        logical_date=next_run_date,
        data_interval=self.timetable.data_interval_for_dagrun(next_run_date),
    )
```

### Algorithm 2: Task Priority Sorting

**Purpose:** Determine which tasks to execute first

```python
def _get_priority_queue(self, session):
    """
    Get tasks ordered by priority

    Priority calculation:
    1. DAG priority_weight
    2. Task priority_weight
    3. Queue priority (if configured)
    4. FIFO (first in, first out)
    """
    return (
        session.query(TaskInstance)
        .filter(TaskInstance.state == State.QUEUED)
        .order_by(
            # Highest priority first
            TaskInstance.priority_weight.desc(),
            # Then by queue date (FIFO)
            TaskInstance.queued_dttm.asc(),
        )
        .all()
    )
```

---

## Performance Considerations

### 1. DAG Parsing Performance

**Configuration:**
```ini
[scheduler]
# How often to scan DAG directory
dag_dir_list_interval = 300  # 5 minutes

# Parse DAGs in parallel
max_dagruns_to_create_per_loop = 10
max_tis_per_query = 512
```

**Optimization Tips:**
- Keep DAG files lightweight
- Avoid heavy computations in DAG file scope
- Use `.airflowignore` to skip files
- Enable DAG serialization

### 2. Database Query Optimization

**Key Queries:**
```sql
-- Finding tasks to schedule
SELECT * FROM task_instance
WHERE state = 'scheduled'
AND execution_date <= NOW()
ORDER BY priority_weight DESC
LIMIT 512;

-- Checking upstream dependencies
SELECT state FROM task_instance
WHERE dag_id = ? AND run_id = ?
AND task_id IN (?, ?, ...);
```

**Optimization:**
- Add database indexes
- Use connection pooling
- Tune `max_tis_per_query`
- Use read replicas for queries

### 3. Scheduler Scalability

**Single Scheduler Limits:**
- ~1000 DAGs
- ~10,000 tasks per minute

**Multiple Schedulers:**
- Airflow 2.0+ supports multiple schedulers
- Each scheduler works on different DAGs
- Shared database coordinates work

**Configuration:**
```ini
[scheduler]
# Number of task instances to check per loop
max_tis_per_query = 512

# Parallelism for DAG parsing
parsing_processes = 2

# How many DagRuns to create per loop
max_dagruns_to_create_per_loop = 10
```

---

## Monitoring and Debugging

### Health Checks

```python
# Check if scheduler is running
SELECT * FROM job
WHERE job_type = 'SchedulerJob'
AND state = 'running'
AND heartbeat > NOW() - INTERVAL '5 minutes';
```

### Common Issues

1. **Scheduler not picking up new DAGs**
   - Check `dag_dir_list_interval`
   - Check for import errors in UI
   - Verify file permissions

2. **Tasks not being scheduled**
   - Check dependencies
   - Verify pool slots available
   - Check concurrency limits

3. **Slow scheduling**
   - Reduce DAG file size
   - Increase `max_tis_per_query`
   - Add database indexes

### Logging

```python
# Enable debug logging
[logging]
logging_level = DEBUG
fab_logging_level = DEBUG

# Scheduler logs location
base_log_folder = /path/to/logs
```

---

## Summary

The Scheduler is a continuous loop that:

1. **Discovers** DAG files from filesystem
2. **Creates** DagRun instances based on schedules
3. **Evaluates** task dependencies
4. **Queues** ready tasks to executor
5. **Monitors** task execution
6. **Updates** state in database

**Key Takeaways:**
- Everything goes through database
- Dependencies are modular and extensible
- Performance tuning is critical at scale
- Multiple schedulers can run concurrently

**GitHub Links:**
- Scheduler Job: https://github.com/apache/airflow/blob/main/airflow-core/src/airflow/jobs/scheduler_job_runner.py
- DAG Parsing: https://github.com/apache/airflow/blob/main/airflow-core/src/airflow/models/dagbag.py
- Dependencies: https://github.com/apache/airflow/tree/main/airflow-core/src/airflow/ti_deps/deps

---

**Document Version**: 1.0
**Last Updated**: 2026-02-20
