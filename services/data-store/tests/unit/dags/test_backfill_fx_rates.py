"""Structural checks for the backfill_fx_rates DAG. See test_daily_pipeline.py's module
docstring for why this only runs where Airflow is importable (it isn't in this workspace's own
venv), and how to run it by hand."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.models import DagBag  # noqa: E402

DAGS_FOLDER = Path(__file__).parents[3] / "dags"


@pytest.fixture(scope="module")
def dagbag():
    sys.path.insert(0, str(DAGS_FOLDER))
    return DagBag(dag_folder=str(DAGS_FOLDER))


def test_backfill_fx_rates_parses_without_errors(dagbag):
    assert dagbag.import_errors == {}
    assert "backfill_fx_rates" in dagbag.dags


def test_backfill_fx_rates_is_unscheduled(dagbag):
    dag = dagbag.dags["backfill_fx_rates"]
    assert dag.schedule is None


def test_backfill_task_depends_on_requested_currencies(dagbag):
    dag = dagbag.dags["backfill_fx_rates"]
    currencies_task = dag.get_task("requested_currencies")
    backfill_task = dag.get_task("backfill")

    assert currencies_task.task_id in backfill_task.upstream_task_ids
