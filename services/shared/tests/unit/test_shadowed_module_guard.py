"""The workspace-wide guard in the repo-root conftest.py that refuses a run in which a test file
was silently replaced by another one with the same module name.

Under the workspace's `--import-mode=importlib`, a test file's module name comes from its
package path. When auth, portfolio, shared and web each had a `tests/unit/__init__.py`, all four
were a package called `unit`, so portfolio's `tests/unit/test_cli.py` and auth's were both
`unit.test_cli`. pytest reused whichever was imported first: portfolio's file was reported as
collected and passing while auth's tests ran in its place, and around 145 portfolio and web
tests never ran in a workspace run (pre-commit, pre-push, CI). The guard makes that a loud
failure instead.
"""

from pathlib import Path

import pytest

_ROOT_CONFTEST = Path(__file__).resolve().parents[4] / "conftest.py"


@pytest.fixture
def inner(pytester):
    pytester.makeconftest(_ROOT_CONFTEST.read_text())
    return pytester


def _make_service(pytester, name, test_name, *, package):
    unit = pytester.path / name / "tests" / "unit"
    unit.mkdir(parents=True)
    if package:
        (unit / "__init__.py").write_text("")
    (unit / "test_x.py").write_text(f"def {test_name}():\n    pass\n")


def test_a_test_file_shadowed_by_a_same_named_module_stops_the_run(inner):
    _make_service(inner, "first", "test_from_first", package=True)
    _make_service(inner, "second", "test_from_second", package=True)

    result = inner.runpytest("--import-mode=importlib")

    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stdout.fnmatch_lines(["*second/tests/unit/test_x.py*first/tests/unit/test_x.py*"])
    assert "test_from_first PASSED" not in result.stdout.str()


def test_same_named_test_files_in_different_directories_run_normally(inner):
    """Without `__init__.py`, importlib names each module after its own path, so both run."""
    _make_service(inner, "first", "test_from_first", package=False)
    _make_service(inner, "second", "test_from_second", package=False)

    result = inner.runpytest("--import-mode=importlib")

    result.assert_outcomes(passed=2)
