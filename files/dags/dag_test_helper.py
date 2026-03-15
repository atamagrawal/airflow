import os
import json
from pathlib import Path
from airflow import settings

def dag_run(dag):
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

def dag_task_run(dag):
    import os
    os.environ['AIRFLOW__CORE__UNIT_TEST_MODE'] = 'True'

    # Execute each task directly
    for task in dag.tasks:
        print(f"Executing task: {task.task_id}")
        task.execute(context={})