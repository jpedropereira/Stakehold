import shlex
from datetime import datetime

from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Param

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
    params={
        "tickers": Param(
            [],
            type="array",
            title="Tickers",
            description=(
                "Restrict this run to these final, post-suffix-resolution tickers (e.g. "
                "'AAPL', '0700.HK') -- matches any currently followed ticker, from fund "
                "holdings or tracked_tickers.yaml. Useful for re-backfilling specific "
                "tickers that previously failed, without waiting on the full universe. "
                "Leave empty to backfill everything followed."
            ),
        ),
    },
    # shlex.quote (not raw Jinja interpolation) so a ticker value from the Trigger DAG config
    # can never break out of its --ticker argument into the shell command.
    user_defined_filters={"shquote": shlex.quote},
) as dag:
    start = EmptyOperator(task_id="start")

    backfill = BashOperator(
        task_id="backfill_historical_ohlcv",
        bash_command=(
            "cd /opt/stakehold/services/data-store && uv run --frozen data-store backfill-ohlcv"
            "{% for t in params.tickers %} --ticker {{ t | shquote }}{% endfor %}"
        ),
    )

    end = EmptyOperator(task_id="end")

    start >> backfill >> end
