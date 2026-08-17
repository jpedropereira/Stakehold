from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

with DAG(
    dag_id="backfill_historical_ohlcv",
    description=(
        "One-off/rare backfill of full historical daily OHLCV for all followed tickers "
        "(fund holdings + tracked_tickers.yaml), fetching only the still-missing stretch "
        "before each ticker's earliest stored date. Deliberately rate-limited against "
        "yfinance and can run for hours -- trigger manually, do not schedule."
    ),
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "prices", "backfill"],
) as dag:
    BashOperator(
        task_id="backfill_historical_ohlcv",
        bash_command=(
            "cd /opt/stakehold/services/data-store && uv run --frozen data-store backfill-ohlcv"
        ),
    )
