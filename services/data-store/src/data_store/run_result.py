"""The result value every run-level operation returns.

Shared vocabulary rather than any one layer's concern: `pipeline` builds these, `jobs` builds
and forwards them, the CLI turns them into exit codes, and Airflow reads them back as XCom
values. Keeping the definition in one place is what stops those four from drifting.
"""

# The keys every run result carries, whatever produced it, so a caller can react to any run
# without knowing which one it was. Deliberately only two: a holdings run counts funds and a
# pricing run counts tickers, so a shared "units" key would mean something different in each
# and be a trap rather than a contract.
RUN_RESULT_KEYS = ("status", "rows_saved")

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_NOTHING_TO_DO = "nothing_to_do"


def build_run_result(
    *, units_total: int, rows_saved: int, failed: int, **extra: int
) -> dict[str, int | str]:
    """Counts plus a derived `status`.

    `units_total` is whatever the run iterates over — tickers for a pricing flow, funds for a
    holdings scrape — and is only used to decide whether there was anything to do at all. Pass
    the meaningful count itself through `extra` under a name that says what it counts.

    `status` exists so callers don't re-derive intent from counts. Zero rows saved is ambiguous
    on its own: it's the correct outcome for a run with nothing to do, and an alarm for a run
    whose every unit failed. Those are exactly the two the logging rules insist must never look
    alike, and this is what lets an automated check tell them apart.

    The return is a plain dict of builtins on purpose. Airflow reads it back across a process
    boundary — pickled in this service's interpreter, unpickled in Airflow's own, where
    `data_store` is not importable — so a dataclass, enum, date or numpy int would fail there
    with ModuleNotFoundError. Boring is the requirement, not an accident.
    """
    # Failure outranks emptiness. A run that ended up with nothing to work on *because*
    # something failed is not a quiet day -- reporting it as `nothing_to_do` would make it
    # green, which is the exact silent success this contract exists to prevent.
    if failed:
        status = STATUS_PARTIAL
    elif units_total == 0:
        status = STATUS_NOTHING_TO_DO
    else:
        status = STATUS_OK
    return {"status": status, "rows_saved": rows_saved, **extra}
