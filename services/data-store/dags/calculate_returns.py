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
def calculate(tickers: list[str]) -> dict:
    from data_store.jobs import run_calculate_returns

    return run_calculate_returns(tickers=tickers or None)


with DAG(
    dag_id="calculate_returns",
    description=(
        "Full rebuild of market_returns (split-adjusted price, as-traded price, and price/"
        "dividend/simple/log returns) from raw_market_data. Manual trigger for now -- run "
        "after ingest-market-data or backfill-market-data land new data."
    ),
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "returns"],
    default_args={"execution_timeout": timedelta(hours=2)},
    params={
        "tickers": Param(
            [],
            type="array",
            title="Tickers",
            description=(
                "Restrict this run to these tickers, as stored in raw_market_data. Leave "
                "empty to rebuild returns for every ticker with stored market data."
            ),
        ),
    },
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    tickers = requested_tickers()
    result = calculate(tickers)
    checked = check_run(result)

    start >> tickers
    checked >> end
