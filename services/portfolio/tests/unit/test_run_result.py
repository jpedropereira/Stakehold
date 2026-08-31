"""The run-result contract: counts in, a status a caller can act on out."""

from portfolio.run_result import (
    RUN_RESULT_KEYS,
    STATUS_NOTHING_TO_DO,
    STATUS_OK,
    STATUS_PARTIAL,
    build_run_result,
)


def test_every_result_carries_the_contract_keys():
    result = build_run_result(units_total=1, rows_saved=1, failed=0)

    assert set(RUN_RESULT_KEYS) <= set(result)


def test_work_done_is_ok():
    result = build_run_result(units_total=3, rows_saved=7, failed=0)

    assert result == {"status": STATUS_OK, "rows_saved": 7}


def test_nothing_to_work_on_is_nothing_to_do():
    result = build_run_result(units_total=0, rows_saved=0, failed=0)

    assert result["status"] == STATUS_NOTHING_TO_DO


def test_a_failure_is_partial():
    result = build_run_result(units_total=3, rows_saved=2, failed=1)

    assert result["status"] == STATUS_PARTIAL


def test_failure_outranks_emptiness():
    """A run left with nothing to do *because* something failed is not a quiet day."""
    result = build_run_result(units_total=0, rows_saved=0, failed=1)

    assert result["status"] == STATUS_PARTIAL


def test_extra_counts_are_passed_through():
    result = build_run_result(units_total=1, rows_saved=4, failed=0, tables_created=4)

    assert result["tables_created"] == 4
