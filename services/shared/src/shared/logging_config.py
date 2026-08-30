"""One place that decides where a service's log output goes.

Without this, `logger.info` is silently dropped: Python's default logging configuration installs
no handler, and its `lastResort` fallback only emits WARNING and above.

**Logs go to stderr, results go to stdout.** Each service's CLI prints its own result (e.g. a row
count) to stdout so the command stays pipeable; everything else -- progress, warnings, errors --
goes to stderr through the logger. See the repo-root and each service's own
`.claude/rules/logging.md` for the full convention.
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """Install a stderr handler on the root logger, once.

    Safe to call from every entry point: a CLI invocation, a direct call from a scheduler task
    (e.g. an Airflow `jobs.run_*` call that never goes through `cli.main`), or a test.
    `basicConfig` is a no-op when the root logger already has a handler, which makes repeated
    calls harmless and leaves a host application's own logging setup alone if this package is
    ever imported into one.
    """
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=LOG_DATE_FORMAT,
        stream=sys.stderr,
    )
