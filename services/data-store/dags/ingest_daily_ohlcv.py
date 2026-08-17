import shlex
from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Param

with DAG(
    dag_id="ingest_daily_ohlcv",
    description=(
        "Fetches daily OHLCV prices for the fund's current holdings and loads daily_ohlcv."
    ),
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "prices"],
    params={
        "tickers": Param(
            [],
            type="array",
            title="Tickers",
            description=(
                "Restrict this run to these final, post-suffix-resolution tickers (e.g. "
                "'AAPL', '0700.HK') -- matches any currently followed ticker, from fund "
                "holdings or tracked_tickers.yaml. Leave empty to price everything followed."
            ),
        ),
    },
    # shlex.quote (not raw Jinja interpolation) so a ticker value from the Trigger DAG config
    # can never break out of its --ticker argument into the shell command.
    user_defined_filters={"shquote": shlex.quote},
) as dag:
    start = EmptyOperator(task_id="start")

    ingest = BashOperator(
        task_id="ingest_daily_ohlcv",
        bash_command=(
            "cd /opt/stakehold/services/data-store && uv run --frozen data-store ingest-ohlcv"
            "{% for t in params.tickers %} --ticker {{ t | shquote }}{% endfor %}"
        ),
    )

    end = EmptyOperator(task_id="end")

    start >> ingest >> end
