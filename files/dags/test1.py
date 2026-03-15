from airflow.decorators import dag
from pendulum import datetime
from airflow.providers.standard.operators.python import PythonOperator

@dag(
    dag_id="my_dag",
    start_date=datetime(2023, 1, 1),
    schedule="@daily",
    catchup=False,
)
def my_dag():
    t1 = PythonOperator(
        task_id="print_hello",
        python_callable=lambda: print("Hello, Airflow!"),
    )

dag_object = my_dag()

if __name__ == "__main__":
    from dag_test_helper import dag_run    
    dag_run(dag_object)
