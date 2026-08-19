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
def backfill(tickers: list[str]) -> dict:
    from data_store.jobs import run_backfill_dividends

    # No earliest_date: the resume default, which skips every ticker that already has data.
    # Pass one only to reach further back than a previous run did -- see
    # pipeline._backfill_window.
    return run_backfill_dividends(tickers=tickers or None)


with DAG(
    dag_id="backfill_dividend_events",
    description=(
        "One-off/rare backfill of full dividend history for all followed tickers "
        "(fund holdings + tracked_tickers.yaml), fetching only what each ticker is "
        "still missing. Deliberately rate-limited and can run for hours -- trigger manually, "
        "do not schedule."
    ),
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "dividends", "backfill"],
    default_args={"execution_timeout": timedelta(hours=12)},
    params={
        "tickers": Param(
            [],
            type="array",
            title="Tickers",
            description=(
                "Restrict this run to these final, post-suffix-resolution tickers (e.g. "
                "'AAPL', '0700.HK'). Useful for re-backfilling specific tickers that "
                "previously failed. Leave empty to backfill everything followed."
            ),
        ),
    },
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # expect_rows=False: once every ticker is covered a rerun correctly saves nothing. That is
    # success here, unlike for the daily ingests.
    start >> check_run(backfill(requested_tickers()), expect_rows=False) >> end
