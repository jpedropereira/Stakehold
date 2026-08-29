from datetime import datetime, timedelta

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.sdk import DAG, Param, get_current_context, task
from common import EXTERNAL_PYTHON_KWARGS, assert_interpreter_available, check_run

assert_interpreter_available()


@task
def requested_currencies() -> list[str]:
    """Read the `currencies` DAG param, in Airflow's own interpreter -- same pattern as
    common.requested_tickers, kept local since this is the only DAG with a currency-code param."""
    params = get_current_context()["params"]
    return [str(currency) for currency in (params.get("currencies") or [])]


@task.external_python(**EXTERNAL_PYTHON_KWARGS)
def backfill(currencies: list[str]) -> dict:
    from data_store.jobs import run_backfill_fx_rates

    # No earliest_date: the resume default, which skips every pair that already has data.
    # Pass one only to reach further back than a previous run did -- see
    # pipeline._backfill_window.
    return run_backfill_fx_rates(currencies=currencies or None)


with DAG(
    dag_id="backfill_fx_rates",
    description=(
        "One-off/rare backfill of full historical currency-conversion rates for every currency "
        "in config/currencies.yaml, fetching only what each pair is still missing. Deliberately "
        "rate-limited -- trigger manually, do not schedule."
    ),
    schedule=None,
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "fx", "backfill"],
    default_args={"execution_timeout": timedelta(hours=2)},
    params={
        "currencies": Param(
            [],
            type="array",
            title="Currencies",
            description=(
                "Restrict this run to these quote currency codes (e.g. 'USD'). Useful for "
                "re-backfilling specific currencies that previously failed. Leave empty to "
                "backfill every currency tracked in config/currencies.yaml."
            ),
        ),
    },
) as dag:
    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # expect_rows=False: once every currency pair is covered a rerun correctly saves nothing.
    # That is success here, unlike for the daily ingest.
    # Bind start to the *head* of the chain explicitly -- see daily_pipeline.py's comment on the
    # same pattern for why this can't be written as one chained expression.
    currencies = requested_currencies()
    result = backfill(currencies)
    checked = check_run(result, expect_rows=False)

    start >> currencies
    checked >> end
