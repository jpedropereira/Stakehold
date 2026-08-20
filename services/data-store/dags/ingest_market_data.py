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
    from data_store.jobs import run_ingest_market_data

    return run_ingest_market_data(tickers=tickers or None)


with DAG(
    dag_id="ingest_market_data",
    description=(
        "Fetches daily OHLCV prices, dividends and stock splits for the fund's current "
        "holdings and loads raw_market_data."
    ),
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "prices", "dividends"],
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

    # Bind start to the *head* of the chain explicitly. Writing this as
    # `start >> check_run(ingest(requested_tickers())) >> end` looks equivalent but is not:
    # Python evaluates inside-out, so the chain is built first and the expression returns the
    # *last* task, leaving start attached to check_run while the real work ran ahead of it,
    # unbracketed.
    tickers = requested_tickers()
    result = ingest(tickers)
    # expect_rows=True (the default): rows_saved counts OHLCV rows specifically, present on
    # every trading day regardless of whether any ticker paid a dividend or split that day, so
    # zero here is still a real anomaly worth failing on -- unlike the old dividends-only DAG,
    # this merged run doesn't need a separate expect_rows=False path, since "dividend rows" is
    # no longer a distinct row-producing save this check could false-alarm on.
    checked = check_run(result)

    start >> tickers
    checked >> end
