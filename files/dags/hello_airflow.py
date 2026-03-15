from airflow import DAG
from airflow.decorators import task
from datetime import datetime

with DAG(
    dag_id="hello_airflow",
    start_date=datetime(2024, 1, 1),
    schedule=None,
    catchup=False,
) as dag:

    @task
    def hello():
        print("Hello Airflow 👋")

    hello()

if __name__ == "__main__":
    from dag_test_helper import dag_task_run    
    dag_task_run(dag)