"""The result value every run-level operation returns.

Shared vocabulary rather than any one layer's concern: `pipeline` builds these, `jobs` builds
and forwards them, and the CLI turns them into exit codes. Keeping the definition in one place
is what stops those three from drifting.

Deliberately a replica of data-store's `run_result.py`, not an import of it, and deliberately
not hoisted into the `shared` package: the two services don't share code across their boundary
any more than they share a database, and `shared/docs/architecture.md` asks for a second
implementation to actually start drifting before this gets promoted.
"""

# The keys every run result carries, whatever produced it, so a caller can react to any run
# without knowing which one it was. Deliberately only two: an import run counts transactions
# and a rebuild counts snapshots, so a shared "units" key would mean something different in
# each and be a trap rather than a contract.
RUN_RESULT_KEYS = ("status", "rows_saved")

STATUS_OK = "ok"
STATUS_PARTIAL = "partial"
STATUS_NOTHING_TO_DO = "nothing_to_do"


def build_run_result(
    *, units_total: int, rows_saved: int, failed: int, **extra: int
) -> dict[str, int | str]:
    """Counts plus a derived `status`.

    `units_total` is whatever the run iterates over — accounts for a rebuild, rows for an
    import — and is only used to decide whether there was anything to do at all. Pass the
    meaningful count itself through `extra` under a name that says what it counts.

    `status` exists so callers don't re-derive intent from counts. Zero rows saved is
    ambiguous on its own: it's the correct outcome for a run with nothing to do, and an alarm
    for a run whose every unit failed. Those are exactly the two the logging rules insist must
    never look alike, and this is what lets an automated check tell them apart. For this
    service that distinction is load-bearing in one more place than data-store's: an unmapped
    instrument blocks its own rebuild and must surface as `partial`, never as a quiet success.

    The return is a plain dict of builtins on purpose — a CLI exit code, an HTTP response body
    and a test assertion all read it, and none of them should need to import a portfolio type.
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
