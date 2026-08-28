"""Structural checks for the daily_pipeline DAG.

Guarded by importorskip: the workspace venv (Python 3.14) deliberately has no `airflow`
installed -- see dags/common.py's module docstring and docs/architecture.md's "two interpreters,
on purpose" for why (Airflow only supports Python <=3.13, which is why `@task.external_python`
exists to bridge the two). This test only executes where Airflow is importable. Nothing in the
repo currently has both `airflow` and `pytest` on one interpreter, so today that means running it
by hand -- e.g. `docker compose exec airflow-scheduler python -m pytest ...` -- against a
container built from Dockerfile.airflow, with `pytest` installed ad hoc since the image doesn't
carry it. It skips cleanly under `uv run pytest`.
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.models import DagBag  # noqa: E402

DAGS_FOLDER = Path(__file__).parents[3] / "dags"


@pytest.fixture(scope="module")
def dagbag():
    # dags/common.py relies on `from common import ...` resolving, which Airflow's own DAG
    # processor arranges by putting the DAG folder on sys.path -- but plain DagBag(dag_folder=...)
    # does not replicate that (only its zip-import path does), so it's done by hand here.
    sys.path.insert(0, str(DAGS_FOLDER))
    return DagBag(dag_folder=str(DAGS_FOLDER))


def test_daily_pipeline_parses_without_errors(dagbag):
    assert dagbag.import_errors == {}
    assert "daily_pipeline" in dagbag.dags


def test_daily_pipeline_schedule(dagbag):
    dag = dagbag.dags["daily_pipeline"]
    assert dag.schedule == "@daily"


def test_regimes_and_market_data_have_no_dependency_between_them(dagbag):
    dag = dagbag.dags["daily_pipeline"]
    regimes_task = dag.get_task("ingest_regimes")
    market_task = dag.get_task("ingest_market_data")

    assert market_task.task_id not in regimes_task.downstream_task_ids
    assert market_task.task_id not in regimes_task.upstream_task_ids
    assert regimes_task.task_id not in market_task.downstream_task_ids
    assert regimes_task.task_id not in market_task.upstream_task_ids


def test_returns_depends_on_market_data_check_not_regimes(dagbag):
    dag = dagbag.dags["daily_pipeline"]
    checked_market_data = dag.get_task("check_market_data_run")
    returns_task = dag.get_task("calculate_returns")
    checked_regimes = dag.get_task("check_regimes_run")

    assert checked_market_data.task_id in returns_task.upstream_task_ids
    assert checked_regimes.task_id not in returns_task.upstream_task_ids
