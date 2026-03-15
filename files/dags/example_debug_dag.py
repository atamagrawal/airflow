from airflow import DAG
from airflow.decorators import task
from datetime import datetime

with DAG(
    dag_id="example_debug_dag",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
    tags=["example", "debug"],
) as dag:

    @task
    def hello():
        print("Hello Airflow 👋")
        return "Task completed successfully!"

    @task
    def goodbye(message: str):
        print(f"Received: {message}")
        print("Goodbye Airflow 👋")

    # Task dependencies
    result = hello()
    goodbye(result)

if __name__ == "__main__":
    # Properly initialize Airflow before running dag.test()
    # This replicates what the test fixture does to make the auto-parsing fix work
    import os
    import json
    from pathlib import Path
    from airflow import settings
    from airflow.configuration import conf

    # Get the directory containing this DAG file
    dag_folder = str(Path(__file__).parent.absolute())

    # Configure DAG bundle - this is what the test fixture does!
    # Without this, DagBundlesManager can't find your DAG for auto-parsing
    bundle_config = [
        {
            "name": "local_debug",
            "classpath": "airflow.dag_processing.bundles.local.LocalDagBundle",
            "kwargs": {"path": dag_folder, "refresh_interval": 0},
        }
    ]
    os.environ['AIRFLOW__DAG_PROCESSOR__DAG_BUNDLE_CONFIG_LIST'] = json.dumps(bundle_config)

    # Set required environment variables
    os.environ.setdefault('AIRFLOW__CORE__EXECUTOR', 'LocalExecutor')
    os.environ.setdefault('AIRFLOW__CORE__LOAD_EXAMPLES', 'False')

    # 1. Initialize Airflow settings and ORM (required by line 1243-1244 of dag.py)
    if settings.Session is None:
        print("Initializing Airflow ORM...")
        settings.configure_orm()

    # 2. Initialize database (required for sync_bag_to_db)
    try:
        from airflow.utils.db import initdb
        print("Ensuring database is initialized...")
        initdb()
    except Exception as e:
        print(f"Database initialization warning: {e}")

    # 3. Now dag.test() will work with auto-parsing (commit 6d977a925)
    # The fix in lines 1273-1298 will:
    # - Check if DAG version exists
    # - If not, use DagBundlesManager to find and parse the DAG
    # - Sync it to the database
    # - Then run the test
    print(f"\nRunning DAG test for: {dag.dag_id}")
    print(f"DAG folder configured as bundle: {dag_folder}\n")
    dag.test()
