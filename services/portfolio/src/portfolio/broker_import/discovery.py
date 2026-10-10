"""Loads BrokerParser/CashflowParser implementations from the entry-point groups any installed
distribution registers under -- portfolio's own, during this repo's transition, and later a
separate plugin package. See docs/broker-import.md.
"""

import logging
from importlib.metadata import entry_points

from portfolio.broker_import.base import BrokerParser, CashflowParser
from portfolio.errors import BrokerPluginError

logger = logging.getLogger(__name__)

BROKER_PARSER_GROUP = "stakehold.broker_parsers"
CASHFLOW_PARSER_GROUP = "stakehold.cashflow_parsers"


def discover_broker_parsers() -> dict[str, BrokerParser]:
    return _discover(BROKER_PARSER_GROUP)


def discover_cashflow_parsers() -> dict[str, CashflowParser]:
    return _discover(CASHFLOW_PARSER_GROUP)


def _discover(group: str) -> dict[str, object]:
    registry: dict[str, object] = {}
    for ep in entry_points(group=group):
        if ep.name in registry:
            raise BrokerPluginError(
                f"two installed plugins both register {ep.name!r} under entry-point group "
                f"{group!r} -- uninstall one before continuing"
            )
        try:
            parser_cls = ep.load()
            parser = parser_cls()
        except Exception as exc:
            raise BrokerPluginError(
                f"entry point {ep.name!r} in group {group!r} failed to load: {exc}"
            ) from exc
        if parser.broker_key != ep.name:
            raise BrokerPluginError(
                f"entry point {ep.name!r} in group {group!r} loaded a parser whose broker_key "
                f"is {parser.broker_key!r} -- these must match"
            )
        registry[ep.name] = parser
        logger.info("broker-plugin discovery: registered %r from group %r.", ep.name, group)
    return registry
