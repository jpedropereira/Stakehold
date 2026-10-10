from datetime import datetime, timedelta

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, task
from common import EXTERNAL_PYTHON_KWARGS, assert_interpreter_available, check_run

assert_interpreter_available()


@task.external_python(**EXTERNAL_PYTHON_KWARGS)
def process_ticker_requests() -> dict:
    from data_store.jobs import run_process_ticker_requests

    return run_process_ticker_requests()


with DAG(
    dag_id="ticker_onboarding",
    description=(
        "Takes each newly added tracked ticker (a user's request, or a new tracked_tickers.yaml "
        "entry) through a full-history backfill, its currency's FX rates and its returns, then "
        "marks it ready for the daily ingest. Frequent so a user waits minutes, not a day."
    ),
    schedule=timedelta(minutes=5),
    start_date=datetime(2024, 1, 1),
    catchup=False,
    # One run at a time: two runs would claim different tickers safely, but two concurrent
    # bursts of yfinance requests are exactly what the per-fetch delay is there to avoid.
    max_active_runs=1,
    tags=["data-store", "prices", "onboarding"],
    default_args={"execution_timeout": timedelta(hours=1)},
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # expect_rows=False: most runs find nothing waiting, and a ticker whose history was already
    # stored (an existing YAML ticker) becomes ready without a new row. Neither is a failure.
    result = process_ticker_requests()
    checked = check_run(result, expect_rows=False)

    start >> result
    checked >> end
