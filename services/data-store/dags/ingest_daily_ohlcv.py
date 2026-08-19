from datetime import datetime, timedelta

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Param, task
from common import (
    EXTERNAL_PYTHON_KWARGS,
    assert_interpreter_available,
    check_run,
    requested_tickers,
)

assert_interpreter_available()


@task.external_python(**EXTERNAL_PYTHON_KWARGS)
def ingest(tickers: list[str]) -> dict:
    # Imports live inside the body because the operator cuts this function out of the DAG into
    # a standalone file; nothing from the surrounding module is available to it.
    from data_store.jobs import run_ingest_ohlcv

    return run_ingest_ohlcv(tickers=tickers or None)


with DAG(
    dag_id="ingest_daily_ohlcv",
    description=(
        "Fetches daily OHLCV prices for the fund's current holdings and loads daily_ohlcv."
    ),
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "prices"],
    default_args={"execution_timeout": timedelta(hours=1)},
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
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    start >> check_run(ingest(requested_tickers())) >> end
