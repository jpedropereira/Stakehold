from datetime import datetime, timedelta

from airflow.sdk import DAG, task
from common import EXTERNAL_PYTHON_KWARGS, assert_interpreter_available, check_run

assert_interpreter_available()


@task.external_python(**EXTERNAL_PYTHON_KWARGS)
def ingest_macro_indicators() -> dict:
    from data_store.jobs import run_ingest_macro_indicators

    return run_ingest_macro_indicators()


@task.external_python(**EXTERNAL_PYTHON_KWARGS)
def calculate_macro_indicator_rates() -> dict:
    from data_store.jobs import run_calculate_macro_indicator_rates

    return run_calculate_macro_indicator_rates()


with DAG(
    dag_id="macro_indicators",
    description=(
        "Monthly refresh of US (FRED) and EU (Eurostat) inflation/GDP indicators: lands "
        "raw_macro_indicators, then rebuilds macro_indicator_rates once the ingest's check "
        "succeeds. Monthly rather than daily -- CPI/HICP update monthly and GDP quarterly, "
        "nothing here changes day to day."
    ),
    schedule="@monthly",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["data-store", "macro", "inflation", "gdp"],
    default_args={"execution_timeout": timedelta(hours=1)},
) as dag:
    ingest_result = ingest_macro_indicators()
    rates_result = calculate_macro_indicator_rates()

    # expect_rows=True (the default): every monthly run should land at least a new CPI/HICP
    # observation, so an "ok" run saving zero rows means something upstream (a bad API key, a
    # dimension filter that stopped matching) went wrong -- same reasoning daily_pipeline uses
    # for regimes/FX ingest.
    checked_ingest = check_run.override(task_id="check_ingest_run")(ingest_result)
    checked_rates = check_run.override(task_id="check_rates_run")(rates_result)

    # Rates rebuild is gated on the ingest's *check* succeeding, not just the task finishing --
    # an "ok" ingest that silently saved zero rows must not unlock a rates rebuild, mirroring
    # daily_pipeline's calculate_returns gating on check_market_data_run.
    checked_ingest >> rates_result
