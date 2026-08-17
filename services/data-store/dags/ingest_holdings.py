from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG

with DAG(
    dag_id="ingest_holdings",
    description="Scrapes iShares ETF holdings and loads them into etf_holdings.",
    schedule="@weekly",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "holdings"],
) as dag:
    start = EmptyOperator(task_id="start")

    ingest = BashOperator(
        task_id="ingest_holdings",
        bash_command=(
            "cd /opt/stakehold/services/data-store && uv run --frozen data-store ingest-holdings"
        ),
    )

    end = EmptyOperator(task_id="end")

    start >> ingest >> end
