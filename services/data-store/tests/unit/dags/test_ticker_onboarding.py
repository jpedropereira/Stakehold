"""Structural checks for the ticker_onboarding DAG. See test_daily_pipeline.py's module
docstring for why this only runs where Airflow is importable (it isn't in this workspace's own
venv), and how to run it by hand."""

import sys
from datetime import timedelta
from pathlib import Path

import pytest

pytest.importorskip("airflow")

from airflow.models import DagBag  # noqa: E402

DAGS_FOLDER = Path(__file__).parents[3] / "dags"


@pytest.fixture(scope="module")
def dagbag():
    sys.path.insert(0, str(DAGS_FOLDER))
    return DagBag(dag_folder=str(DAGS_FOLDER))


def test_ticker_onboarding_parses_without_errors(dagbag):
    assert dagbag.import_errors == {}
    assert "ticker_onboarding" in dagbag.dags


def test_ticker_onboarding_runs_every_five_minutes_one_at_a_time(dagbag):
    dag = dagbag.dags["ticker_onboarding"]
    assert dag.schedule == timedelta(minutes=5)
    assert dag.max_active_runs == 1


def test_check_run_follows_the_onboarding_task(dagbag):
    dag = dagbag.dags["ticker_onboarding"]
    assert "process_ticker_requests" in dag.get_task("check_run").upstream_task_ids
