from datetime import datetime

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG

with DAG(
    dag_id="example_placeholder",
    description="Phase 1 placeholder proving the scheduler and UI work end to end.",
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["placeholder"],
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    start >> end
