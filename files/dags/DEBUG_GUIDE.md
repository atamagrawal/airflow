# DAG Debugging Guide with VSCode and LocalExecutor

This guide explains how to debug Airflow DAGs locally using VSCode with LocalExecutor.

## Quick Start

See `example_debug_dag.py` for a complete working example with proper initialization code.

## Prerequisites

1. **Setup local virtual environment with UV:**
   ```bash
   # From repository root
   uv sync --all-packages
   ```

2. **Initialize Airflow database:**
   ```bash
   source .venv/bin/activate
   airflow db init
   ```

## Method 1: VSCode Debugger (Recommended)

### Step 1: Add `__main__` Block to Your DAG

Add this at the end of your DAG file (see `example_debug_dag.py` for the complete working version):

```python
if __name__ == "__main__":
    import os
    import json
    from pathlib import Path
    from airflow import settings

    # Get the directory containing this DAG file
    dag_folder = str(Path(__file__).parent.absolute())

    # Configure DAG bundle - enables auto-parsing fix from commit 6d977a9
    bundle_config = [
        {
            "name": "local_debug",
            "classpath": "airflow.dag_processing.bundles.local.LocalDagBundle",
            "kwargs": {"path": dag_folder, "refresh_interval": 0},
        }
    ]
    os.environ['AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_CONFIG_LIST'] = json.dumps(bundle_config)
    os.environ.setdefault('AIRFLOW__CORE__EXECUTOR', 'LocalExecutor')
    os.environ.setdefault('AIRFLOW__CORE__LOAD_EXAMPLES', 'False')

    # Initialize ORM (prevents RuntimeError at dag.py:1243)
    if settings.Session is None:
        print("Initializing Airflow ORM...")
        settings.configure_orm()

    # Initialize database (required for sync_bag_to_db)
    try:
        from airflow.utils.db import initdb
        print("Ensuring database is initialized...")
        initdb()
    except Exception as e:
        print(f"Database initialization warning: {e}")

    # Now dag.test() works with auto-parsing!
    print(f"\nRunning DAG test for: {dag.dag_id}")
    print(f"DAG folder configured as bundle: {dag_folder}\n")
    dag.test()
```

**Why this is needed:**

A fix was added in [commit 6d977a9](https://github.com/apache/airflow/commit/6d977a925e83b5b55003efbcde85cce9ff836ba8) to auto-parse and sync DAGs if not serialized. However, this fix has **3 hard requirements**:

1. **ORM must be configured** - `settings.configure_orm()` must be called first, or `dag.test()` raises `RuntimeError` at line 1243-1244 of `dag.py`
2. **Database must be initialized** - `initdb()` creates the tables needed for `sync_bag_to_db()`
3. **DAG bundle must be configured** - `DagBundlesManager` needs to know where to find your DAG files via `dag_bundle_config_list` config

The test that validates the auto-parsing fix uses a `configure_testing_dag_bundle` fixture to set up the bundle config. When running standalone DAG files, we must replicate this setup.

Without all three, you get: `"Cannot run DagRun for DAG because the dag is not serialized"`

### Step 2: Create VSCode Launch Configuration

Create or update `.vscode/launch.json`:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Debug Airflow DAG",
            "type": "debugpy",
            "request": "launch",
            "program": "${workspaceFolder}/files/dags/example_debug_dag.py",
            "console": "integratedTerminal",
            "env": {
                "PYTHONUNBUFFERED": "1",
                "AIRFLOW__CORE__EXECUTOR": "LocalExecutor",
                "AIRFLOW__CORE__LOAD_EXAMPLES": "False"
            },
            "python": "${workspaceFolder}/.venv/bin/python"
        }
    ]
}
```

**Note:** Change `program` to point to your specific DAG file.

### Step 3: Set Python Interpreter in VSCode

1. Press `Cmd+Shift+P` (macOS) or `Ctrl+Shift+P` (Windows/Linux)
2. Type: `Python: Select Interpreter`
3. Choose: `./.venv/bin/python`
4. Restart VSCode

### Step 4: Debug

1. Open your DAG file
2. Set breakpoints by clicking left of line numbers
3. Press `F5` or select "Run and Debug" → "Debug Airflow DAG"
4. Debugger will pause at breakpoints

## Method 2: Breeze Shell (Most Reliable)

Breeze provides a containerized environment that matches CI/production:

```bash
# Copy DAG to dev folder (mounted in Breeze)
cp files/dags/example_debug_dag.py dev/

# Start Breeze shell
breeze shell

# Inside Breeze, run your DAG
python /opt/airflow/dev/example_debug_dag.py
```

### Running Specific Tasks in Breeze

```bash
# Test entire DAG
breeze run airflow dags test example_debug_dag 2024-01-01

# Test specific task (hello is one of the tasks in the DAG)
breeze run airflow tasks test example_debug_dag hello 2024-01-01
```

## Method 3: Command Line with UV

```bash
# Activate virtual environment
source .venv/bin/activate

# Run DAG directly
python files/dags/example_debug_dag.py

# Or use uv run
uv run --project airflow-core python files/dags/example_debug_dag.py
```

## How the Auto-Parsing Fix Works (Commit 6d977a9)

The fix in `task-sdk/src/airflow/sdk/definitions/dag.py` lines 1273-1298 works like this:

```python
# Check if DAG version exists in database
version = DagVersion.get_version(self.dag_id)
if not version:
    # DAG not serialized yet - auto-parse it!
    manager = DagBundlesManager()
    manager.sync_bundles_to_db(session=session)  # Load bundle configs

    # Parse all DAG files from configured bundles
    for bundle in manager.get_all_dag_bundles():
        dagbag = BundleDagBag(dag_folder=bundle.path, ...)
        sync_bag_to_db(dagbag, bundle.name, bundle.version)  # Serialize to DB

        version = DagVersion.get_version(self.dag_id)
        if version:
            break  # Found and serialized our DAG!
```

**The catch:** `DagBundlesManager` only searches in **configured bundles**. In tests, this is set up by the `configure_testing_dag_bundle` fixture. For standalone scripts, we must configure it manually via:

```python
os.environ['AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_CONFIG_LIST'] = json.dumps([
    {"name": "local_debug", "classpath": "...LocalDagBundle",
     "kwargs": {"path": "/path/to/dags"}}
])
```

See `example_debug_dag.py` for the complete implementation.

## Understanding dag.test() vs Direct Task Execution

### dag.test() (Recommended)
- **What it does:** Simulates a complete DAG run including:
  - DAG serialization and versioning
  - DagRun creation in database
  - Task instance lifecycle management
  - Dependency checking and scheduling logic
  - Proper state management (queued → running → success/failed)
  - Callbacks (on_success_callback, on_failure_callback)

- **Why use it:** Tests the DAG exactly as the scheduler would run it
- **Requirements:**
  - ORM configured (`settings.configure_orm()`)
  - Database initialized (`initdb()`)
  - DAG version in database

### Direct Task Execution
- **What it does:** Runs `task.execute(context={})` directly on each task
- **Why not recommended:**
  - Bypasses scheduler logic
  - No dependency checking
  - No state management
  - Doesn't test serialization
  - Missing DAG-level features (callbacks, etc.)

**Bottom line:** Always use `dag.test()` for proper debugging. The initialization code in `example_debug_dag.py` ensures it works correctly.

## Troubleshooting

### Error: "Cannot run DagRun for DAG because the dag is not serialized"

**Root cause:** The auto-parsing fix (commit 6d977a9) is present in the code but has 3 prerequisites:

1. **ORM configured** - Without `settings.configure_orm()`, you get `RuntimeError: Session not configured`
2. **Database initialized** - Without `initdb()`, the sync operations fail
3. **DAG bundle configured** - Without setting `dag_bundle_config_list`, `DagBundlesManager` can't find your DAG file to auto-parse it

**Solution:** Use the complete initialization code in `example_debug_dag.py` which:
- Configures the ORM: `settings.configure_orm()`
- Initializes database: `initdb()`
- Configures DAG bundle: Sets `AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_CONFIG_LIST` environment variable

**Quick verification:**
```bash
# Check if auto-parsing works
source .venv/bin/activate
python files/dags/example_debug_dag.py

# Should print:
# "Initializing Airflow ORM..."
# "Ensuring database is initialized..."
# "DAG folder configured as bundle: /path/to/files/dags"
# Then successfully run the DAG
```

### Error: "No module named 'airflow'"

**Solution:** Install Airflow in virtual environment
```bash
uv sync --all-packages
```

Then select the correct Python interpreter in VSCode.

### Error: Import errors for providers

**Solution:** Install specific provider dependencies
```bash
# For example, Amazon provider
uv sync --package apache-airflow-providers-amazon
```

## Database Configuration (Optional)

To use MySQL instead of SQLite:

1. **Install MySQL client:**
   ```bash
   source .venv/bin/activate
   pip install PyMySQL
   ```

2. **Update `~/airflow/airflow.cfg`:**
   ```ini
   sql_alchemy_conn = mysql+pymysql://root:@127.0.0.1:23306/airflow?charset=utf8mb4
   ```

## Tips

- **Place debug scripts in `dev/` folder** - it's mounted in Breeze as `/opt/airflow/dev/`
- **Never run commands directly on host** - use `breeze` for reproducibility
- **Enable debug logging:**
  ```bash
  export AIRFLOW__LOGGING__LOGGING_LEVEL=DEBUG
  ```
- **Use Python debugger in code:**
  ```python
  import pdb; pdb.set_trace()
  ```

## References

- [Contributors Quick Start (VSCode)](../../contributing-docs/quick-start-ide/contributors_quick_start_vscode.rst)
- [Local Virtual Environment Guide](../../contributing-docs/07_local_virtualenv.rst)
- [CLAUDE.md - Project Instructions](../../CLAUDE.md)
