from datetime import datetime, timedelta

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, task
from common import EXTERNAL_PYTHON_KWARGS, assert_interpreter_available, check_run

assert_interpreter_available()


@task.external_python(**EXTERNAL_PYTHON_KWARGS)
def ingest() -> dict:
    from data_store.jobs import run_ingest_holdings

    return run_ingest_holdings()


with DAG(
    dag_id="ingest_holdings",
    description="Scrapes iShares ETF holdings and loads them into etf_holdings.",
    schedule="@weekly",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "holdings"],
    # Scraping drives a real browser against a third-party site. Generous, but finite: an
    # untimed hang would hold an executor slot indefinitely without ever failing.
    default_args={"execution_timeout": timedelta(hours=1)},
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    start >> check_run(ingest()) >> end
