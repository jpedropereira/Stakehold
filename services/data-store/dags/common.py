"""Shared pieces for the data-store DAGs.

Not a DAG file -- listed in `.airflowignore` so the dag-processor doesn't scan it for DAGs.
Airflow puts the DAGs folder on `sys.path` while parsing, which is what makes `from common
import ...` work from the DAG files beside it.

The DAGs call `data_store` through `@task.external_python` rather than shelling out to the CLI.
That runs the job in this project's own interpreter -- which it must, since the workspace needs
Python 3.14 and the Airflow image ships 3.13 -- while handing the job's return value back to
Airflow as an XCom. The result is that a task reports *what happened*, not merely that a shell
exited zero, which is what makes the checks below possible at all.
"""

import os
from pathlib import Path

from airflow.sdk import get_current_context, task
from airflow.sdk.exceptions import AirflowFailException

# The interpreter that has this project's dependencies. Set in docker-compose.yml; the default
# matches where Dockerfile.airflow puts the venv.
DATA_STORE_PYTHON = os.environ.get("DATA_STORE_PYTHON", "/opt/stakehold/.venv/bin/python")

# Everything the external tasks need in common. `expect_airflow=False` is required, not
# cosmetic: the operator otherwise probes the target venv for Airflow and fails, and this venv
# deliberately doesn't have it -- keeping the app's dependencies out of Airflow's environment is
# the whole reason for running the job out-of-process.
#
# Expect a `ModuleNotFoundError: No module named 'pendulum'` traceback at ERROR level in the task
# log just before the task body runs. It is noise, not a failure: the operator probes the target
# venv for pendulum to decide whether it can pass datetime context variables across, and these
# tasks take plain arguments and want none of them. Installing pendulum purely to quiet the log
# would add a dependency for no behaviour.
EXTERNAL_PYTHON_KWARGS = {"python": DATA_STORE_PYTHON, "expect_airflow": False}


def assert_interpreter_available() -> None:
    """Fail at parse time if the interpreter is missing, rather than at run time.

    A wrong path would otherwise surface only when a task first runs, as a confusing exec error.
    Raising here turns it into an import error visible against the DAG in the UI, which is both
    louder and earlier. It also catches the common case of the image being stale.
    """
    if not Path(DATA_STORE_PYTHON).exists():
        raise RuntimeError(
            f"data-store interpreter not found at {DATA_STORE_PYTHON}. The image may need "
            "rebuilding (docker compose build), or DATA_STORE_PYTHON may be set wrong."
        )


@task
def requested_tickers() -> list[str]:
    """Read the `tickers` DAG param, in Airflow's own interpreter.

    Kept as a separate plain task so the external one takes an ordinary `list[str]` argument.
    Airflow's context object doesn't cross the process boundary -- the operator documents `var`
    and `ti` as unsupported -- so reading params here and passing the plain value across avoids
    the problem rather than working around it.
    """
    params = get_current_context()["params"]
    return [str(ticker) for ticker in (params.get("tickers") or [])]


@task
def check_run(result: dict, *, expect_rows: bool = True) -> dict:
    """Turn a run result into a green or red task.

    This is the point of the whole exercise: a `BashOperator` could only report whether a shell
    exited zero, so a run that silently saved nothing looked identical to a healthy one.

    - `partial` always fails. Some unit of work was lost, and the run said so.
    - `nothing_to_do` always passes. Having nothing to do is a legitimate outcome and must not
      page anyone -- the distinction this whole result contract exists to carry.
    - `ok` with zero rows fails **only** where rows are expected. A daily ingest resolves
      tickers over a multi-day window, so saving nothing means something is wrong even though
      no individual step reported an error -- exactly the silent failure this check is for. A
      backfill legitimately saves nothing once every ticker is already covered, so it passes
      `expect_rows=False`.
    """
    status = result["status"]
    rows = result["rows_saved"]

    if status == "partial":
        raise AirflowFailException(f"run only partly succeeded: {result}")

    if status == "nothing_to_do":
        print(f"nothing to do this run: {result}")
        return result

    if expect_rows and rows == 0:
        raise AirflowFailException(
            "run reported success but saved zero rows, which means no step failed yet nothing "
            f"was written -- check for unresolvable tickers or an upstream data problem: {result}"
        )

    print(f"run complete: {result}")
    return result
