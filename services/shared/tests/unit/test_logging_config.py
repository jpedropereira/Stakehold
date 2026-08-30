import logging
import sys

from shared.logging_config import LOG_DATE_FORMAT, LOG_FORMAT, configure_logging


def test_configure_logging_installs_a_stderr_handler_with_the_documented_format(monkeypatch):
    calls = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))

    configure_logging()

    assert calls == [
        {
            "level": logging.INFO,
            "format": LOG_FORMAT,
            "datefmt": LOG_DATE_FORMAT,
            "stream": sys.stderr,
        }
    ]


def test_configure_logging_passes_through_a_custom_level(monkeypatch):
    calls = []
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: calls.append(kwargs))

    configure_logging(level=logging.DEBUG)

    assert calls[0]["level"] == logging.DEBUG


def test_configure_logging_is_safe_to_call_repeatedly():
    configure_logging()
    configure_logging()
