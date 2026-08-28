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
def ingest_regimes() -> dict:
    from data_store.jobs import run_ingest_regimes

    return run_ingest_regimes()


@task.external_python(**EXTERNAL_PYTHON_KWARGS, execution_timeout=timedelta(hours=1))
def ingest_market_data(tickers: list[str]) -> dict:
    from data_store.jobs import run_ingest_market_data

    return run_ingest_market_data(tickers=tickers or None)


@task.external_python(**EXTERNAL_PYTHON_KWARGS, execution_timeout=timedelta(hours=2))
def calculate_returns(tickers: list[str]) -> dict:
    from data_store.jobs import run_calculate_returns

    return run_calculate_returns(tickers=tickers or None)


with DAG(
    dag_id="daily_pipeline",
    description=(
        "Daily data refresh: ingests regimes and market data in parallel (unrelated sources, "
        "no dependency between them), then rebuilds market_returns once market data ingest "
        "succeeds."
    ),
    schedule="@daily",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "prices", "dividends", "returns", "regimes"],
    default_args={"execution_timeout": timedelta(hours=2)},
    params={
        "tickers": Param(
            [],
            type="array",
            title="Tickers",
            description=(
                "Restrict this run's market-data-ingest and returns-calculation tasks to these "
                "final, post-suffix-resolution tickers (e.g. 'AAPL', '0700.HK') -- matches any "
                "currently followed ticker, from fund holdings or tracked_tickers.yaml. Has no "
                "effect on regimes ingest, which has no ticker concept. Leave empty to run both "
                "over everything followed."
            ),
        ),
    },
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # Bind start to the *head* of each branch explicitly. Writing this as
    # `start >> check_run(ingest_market_data(requested_tickers())) >> end` looks equivalent but
    # is not: Python evaluates inside-out, so the chain is built first and the expression
    # returns the *last* task, leaving start attached to check_run while the real work ran
    # ahead of it, unbracketed.
    tickers = requested_tickers()
    regimes_result = ingest_regimes()

    market_data_result = ingest_market_data(tickers)
    returns_result = calculate_returns(tickers)

    # Each branch gets its own check_run task_id -- left to auto-suffix (check_run, check_run__1,
    # check_run__2) it would still work, but the UI wouldn't tell you which branch failed.
    #
    # expect_rows=True (the default) on regimes too: run_ingest_regimes reports "nothing_to_do",
    # not "ok" with zero rows, when the CSV itself is empty -- see build_run_result -- so this can
    # only fire when the CSV has entries but the DB write silently saved none, exactly the silent
    # failure this check exists to catch.
    checked_regimes = check_run.override(task_id="check_regimes_run")(regimes_result)
    checked_market_data = check_run.override(task_id="check_market_data_run")(market_data_result)
    checked_returns = check_run.override(task_id="check_returns_run")(returns_result)

    start >> tickers
    start >> regimes_result

    # Returns is gated on market-data-ingest's *check* succeeding, not just the raw task
    # finishing -- an "ok" run that saved zero rows must not unlock a returns rebuild any more
    # than a raised exception would. Regimes has no bearing on this: unrelated source, so no edge
    # from either regimes task into the returns branch.
    checked_market_data >> returns_result

    checked_regimes >> end
    checked_returns >> end
