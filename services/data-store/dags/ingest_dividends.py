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

    # Bind start to the *head* of the chain explicitly. Writing this as
    # `start >> check_run(ingest(requested_tickers())) >> end` looks equivalent but is not:
    # Python evaluates inside-out, so the chain is built first and the expression returns the
    # *last* task, leaving start attached to check_run while the real work ran ahead of it,
    # unbracketed.
    tickers = requested_tickers()
    result = ingest(tickers)
    # expect_rows=False, unlike the price ingest. Dividends are genuinely sparse: on most days
    # no followed ticker goes ex-dividend, the source correctly returns nothing, and the run
    # ends "ok" with zero rows. Failing on that would redden this DAG most days -- a daily
    # false alarm is worse than no alarm, because it teaches everyone to ignore red.
    #
    # The cost is real and worth stating: within a single run, "no ticker paid a dividend" and
    # "the fetch is silently broken" look identical, so this DAG cannot catch the latter. What
    # would catch it is a longer-horizon check -- no dividend rows written in N days -- which
    # needs cross-run state and does not belong in a per-run task. `partial` (a failed group)
    # and an unmatched --ticker still fail here.
    checked = check_run(result, expect_rows=False)

    start >> tickers
    checked >> end
