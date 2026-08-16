from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

with DAG(
    dag_id="ingest_daily_ohlcv",
    description=(
        "Fetches daily OHLCV prices for the fund's current holdings and loads daily_ohlcv."
    ),
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "prices"],
) as dag:
    BashOperator(
        task_id="ingest_daily_ohlcv",
        bash_command=(
            "cd /opt/stakehold/services/data-store && uv run --frozen data-store ingest-ohlcv"
        ),
    )
