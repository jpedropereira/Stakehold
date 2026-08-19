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
    from data_store.jobs import run_ingest_dividends

    return run_ingest_dividends(tickers=tickers or None)


with DAG(
    dag_id="ingest_dividends",
    description=(
        "Fetches dividend events for the fund's current holdings and loads dividend_events."
    ),
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "dividends"],
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

    # Dividends are genuinely sparse: on most days no followed ticker goes ex-dividend, and the
    # flow reports that as "nothing to do" rather than an empty success. expect_rows stays True
    # so an "ok" run that somehow saved nothing is still caught.
    start >> check_run(ingest(requested_tickers())) >> end
