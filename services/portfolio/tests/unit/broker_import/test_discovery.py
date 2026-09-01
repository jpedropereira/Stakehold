"""discovery.py tested against a fake entry_points() -- no real installed distribution needed,
so these tests don't depend on what's actually registered in this environment's site-packages.
"""

import pytest
from portfolio.broker_import import discovery
from portfolio.errors import BrokerPluginError


class FakeParser:
    def __init__(self, broker_key):
        self.broker_key = broker_key


class FakeEntryPoint:
    def __init__(self, name, parser_cls):
        self.name = name
        self._parser_cls = parser_cls

    def load(self):
        return self._parser_cls


def _fake_entry_points(groups: dict[str, list[FakeEntryPoint]]):
    def entry_points(*, group):
        return groups.get(group, [])

    return entry_points


def test_discover_returns_empty_dict_for_a_group_with_no_entries(monkeypatch):
    monkeypatch.setattr(discovery, "entry_points", _fake_entry_points({}))

    assert discovery.discover_broker_parsers() == {}
    assert discovery.discover_cashflow_parsers() == {}


def test_discover_registers_one_valid_entry_under_its_name(monkeypatch):
    acme_cls = lambda: FakeParser("acme")  # noqa: E731
    monkeypatch.setattr(
        discovery,
        "entry_points",
        _fake_entry_points({discovery.BROKER_PARSER_GROUP: [FakeEntryPoint("acme", acme_cls)]}),
    )

    parsers = discovery.discover_broker_parsers()

    assert set(parsers) == {"acme"}
    assert parsers["acme"].broker_key == "acme"


def test_discover_rejects_two_entries_claiming_the_same_name_in_one_group(monkeypatch):
    one = lambda: FakeParser("acme")  # noqa: E731
    two = lambda: FakeParser("acme")  # noqa: E731
    monkeypatch.setattr(
        discovery,
        "entry_points",
        _fake_entry_points(
            {
                discovery.BROKER_PARSER_GROUP: [
                    FakeEntryPoint("acme", one),
                    FakeEntryPoint("acme", two),
                ]
            }
        ),
    )

    with pytest.raises(BrokerPluginError):
        discovery.discover_broker_parsers()


def test_discover_rejects_a_broker_key_that_disagrees_with_the_registered_name(monkeypatch):
    mismatched = lambda: FakeParser("globex")  # noqa: E731
    monkeypatch.setattr(
        discovery,
        "entry_points",
        _fake_entry_points({discovery.BROKER_PARSER_GROUP: [FakeEntryPoint("acme", mismatched)]}),
    )

    with pytest.raises(BrokerPluginError):
        discovery.discover_broker_parsers()


def test_discover_broker_and_cashflow_groups_dont_interfere(monkeypatch):
    broker_cls = lambda: FakeParser("acme")  # noqa: E731
    cashflow_cls = lambda: FakeParser("acme")  # noqa: E731
    monkeypatch.setattr(
        discovery,
        "entry_points",
        _fake_entry_points(
            {
                discovery.BROKER_PARSER_GROUP: [FakeEntryPoint("acme", broker_cls)],
                discovery.CASHFLOW_PARSER_GROUP: [FakeEntryPoint("acme", cashflow_cls)],
            }
        ),
    )

    assert set(discovery.discover_broker_parsers()) == {"acme"}
    assert set(discovery.discover_cashflow_parsers()) == {"acme"}
