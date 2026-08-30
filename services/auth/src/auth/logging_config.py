"""One place that decides where this service's log output goes.

Without this, `logger.info` is silently dropped: Python's default configuration installs no
handler, and its `lastResort` fallback only emits WARNING and above.

**Logs go to stderr, results go to stdout.** `auth db init` prints its row count on stdout and
everything else on stderr, so the command stays pipeable.
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """Install a stderr handler on the root logger, once.

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
