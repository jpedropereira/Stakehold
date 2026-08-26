"""One place that decides where this service's log output goes.

Without this, `logger.info` is silently dropped: Python's default configuration installs no
handler, and its `lastResort` fallback only emits WARNING and above. That's why progress
reporting here used to be `print`. Configuring logging once at each entry point removes that
constraint, and with it the reason for the two-mechanism split.

**Logs go to stderr, results go to stdout.** `data-store ingest-market-data` prints its row count on
stdout and everything else on stderr, so the command stays pipeable locally. Airflow sees both
either way: its `BashOperator` runs the subprocess with `stderr=STDOUT` (verified against the
installed `SubprocessHook`), and a `@task.external_python` task captures both streams too.

The other thing a single stream buys: progress and warnings are now ordered relative to each
other. Previously progress went to stdout and warnings to stderr, so in a captured task log a
warning about an unmapped exchange could appear nowhere near the ticker it concerned.
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """Install a stderr handler on the root logger, once.

    Safe to call from every entry point, and it needs to be: a CLI invocation comes through
    `cli.main`, but an Airflow task calls a `jobs.run_*` function directly and never touches
    `main`. `basicConfig` is a no-op when the root logger already has a handler, which makes
    the repeated calls harmless and leaves a host application's own logging setup alone if this
    package is ever imported into one.
    """
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        stream=sys.stderr,
    )
