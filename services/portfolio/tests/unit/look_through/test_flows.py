"""look_through/flows.py: classifies each held ticker, fetches the funds' snapshots from
data-store, isolates a failure to the asset it belongs to, and hands the rest to
look_through/calculator.py. The arithmetic is test_calculator.py's; these tests check what
reaches it and what's reported."""

import logging
import threading
from datetime import date
from decimal import Decimal

import pytest
from portfolio.datastore_client.base import (
    EtfConstituent,
    EtfFund,
    EtfHoldings,
    TickerInfo,
    TrackedTicker,
)
from portfolio.errors import DataStoreApiError
from portfolio.look_through import flows
from portfolio.look_through.calculator import COMMODITY, DIRECT, LOOK_THROUGH, UNMAPPED

EUR = TickerInfo(exists=True, has_returns=True, currency="EUR", fx_currency="EUR")
USD = TickerInfo(exists=True, has_returns=True, currency="USD", fx_currency="USD")
UNKNOWN = TickerInfo(exists=False, has_returns=False)

WORLD_FUND = EtfFund("world", "World", "IWDA.AS", True, date(2026, 8, 30))
NEVER_SCRAPED = EtfFund("small", "Small", "IUSN.DE", True, None)

WORLD = EtfHoldings(
    fund_key="world",
    ticker="IWDA.AS",
    as_of_date=date(2026, 8, 30),
    constituents=[
        EtfConstituent(
            "BRKB",
            "BRK-B",
            "BERKSHIRE HATHAWAY INC CLASS B",
            "Financials",
            "Equity",
            "United States",
            "NYSE",
            "USD",
            Decimal("100"),
        )
    ],
)


def _tracked(ticker, asset_type):
    return TrackedTicker(
        ticker=ticker,
        display_name=ticker,
        asset_type=asset_type,
        currency="EUR",
        exchange=None,
        origin="config",
        status="ready",
        enabled=True,
        attempts=0,
        last_error=None,
        requested_by=None,
        requested_at="2026-10-10T00:00:00Z",
        ready_at=None,
    )


class _FakeDataStoreClient:
    """Canned answers. An Exception instance as the answer is raised instead. The flow calls
    from worker threads, hence the lock."""

    def __init__(self, *, info, tracked=(), funds=(WORLD_FUND, NEVER_SCRAPED), holdings=None):
        self._info = info
        self._tracked = tracked
        self._funds = funds
        self._holdings = {"world": WORLD} if holdings is None else holdings
        self._lock = threading.Lock()
        self.calls = []

    def _answer(self, call, answer):
        with self._lock:
            self.calls.append(call)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get_ticker_info(self, ticker):
        return self._answer(("info", ticker), self._info[ticker])

    def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        answer = self._tracked
        if not isinstance(answer, Exception):
            answer = [t for t in answer if t.ticker in tickers]
        return self._answer(("tracked", tuple(tickers)), answer)

    def list_etf_funds(self):
        return self._answer(("funds",), self._funds)

    def get_latest_etf_holdings(self, fund_key):
        return self._answer(("holdings", fund_key), self._holdings.get(fund_key))


MIX = [
    ("IWDA.AS", Decimal("40")),
    ("BRK-B", Decimal("20")),
    ("VWCE.DE", Decimal("20")),
    ("EGLN.L", Decimal("10")),
    ("IUSN.DE", Decimal("10")),
]


def _client(**kwargs):
    defaults = dict(
        info={"IWDA.AS": EUR, "BRK-B": USD, "VWCE.DE": EUR, "EGLN.L": EUR, "IUSN.DE": EUR},
        tracked=[
            _tracked("IWDA.AS", "etf"),
            _tracked("VWCE.DE", "etf"),
            _tracked("IUSN.DE", "etf"),
            # The wrong type data-store really has: the instrument table wins.
            _tracked("EGLN.L", "equity"),
        ],
    )
    return _FakeDataStoreClient(**{**defaults, **kwargs})


def _by_ticker(run):
    return {a.ticker: a for a in run.assets}


def test_each_held_ticker_is_classified():
    run = flows.look_through_mix(_client(), MIX)
    assets = _by_ticker(run)
    assert {t: a.kind for t, a in assets.items()} == {
        "IWDA.AS": LOOK_THROUGH,
        "BRK-B": DIRECT,
        "VWCE.DE": UNMAPPED,
        "EGLN.L": COMMODITY,
        "IUSN.DE": UNMAPPED,
    }
    assert assets["BRK-B"].matched_in == "IWDA.AS"
    assert assets["VWCE.DE"].note == "no holdings data for this ETF"
    assert assets["IUSN.DE"].note == "holdings never ingested"
    assert all(a.status == flows.INCLUDED for a in run.assets)


def test_weights_are_normalised_over_the_included_assets():
    run = flows.look_through_mix(_client(), MIX)
    assert {a.ticker: a.weight for a in run.assets} == {
        "IWDA.AS": Decimal("0.4"),
        "BRK-B": Decimal("0.2"),
        "VWCE.DE": Decimal("0.2"),
        "EGLN.L": Decimal("0.1"),
        "IUSN.DE": Decimal("0.1"),
    }


def test_the_direct_stock_and_the_fund_line_are_one_name():
    run = flows.look_through_mix(_client(), MIX)
    top = run.exposures.names.top[0]
    assert (top.issuer, top.weight, top.sources) == (
        "BERKSHIRE HATHAWAY INC",
        Decimal("0.6"),
        ("BRK-B", "IWDA.AS"),
    )


def test_only_funds_with_a_snapshot_are_fetched():
    client = _client()
    flows.look_through_mix(client, MIX)
    assert [c for c in client.calls if c[0] == "holdings"] == [("holdings", "world")]


def test_an_untracked_ticker_is_left_out_and_the_rest_renormalised():
    client = _client(info={"IWDA.AS": EUR, "NOPE": UNKNOWN})
    run = flows.look_through_mix(client, [("IWDA.AS", Decimal("3")), ("NOPE", Decimal("1"))])
    assets = _by_ticker(run)
    assert (assets["NOPE"].status, assets["NOPE"].reason, assets["NOPE"].weight) == (
        flows.NOT_TRACKED,
        "not tracked",
        None,
    )
    assert assets["IWDA.AS"].weight == Decimal("1")
    assert run.exposures.assets[0].ticker == "IWDA.AS"


def test_a_failed_ticker_info_call_leaves_out_only_that_ticker(caplog):
    client = _client(info={"IWDA.AS": EUR, "BRK-B": DataStoreApiError("boom")})
    with caplog.at_level(logging.INFO, logger="portfolio.look_through.flows"):
        run = flows.look_through_mix(client, [("IWDA.AS", Decimal("1")), ("BRK-B", Decimal("1"))])
    assets = _by_ticker(run)
    assert (assets["BRK-B"].status, assets["BRK-B"].reason) == (
        flows.FAILED,
        "couldn't reach data-store",
    )
    error = next(r for r in caplog.records if r.levelno == logging.ERROR)
    assert "BRK-B" in error.getMessage() and error.exc_info is not None


def test_a_failed_snapshot_fetch_makes_the_fund_unmapped_not_fatal(caplog):
    client = _client(holdings={"world": DataStoreApiError("boom")})
    with caplog.at_level(logging.INFO, logger="portfolio.look_through.flows"):
        run = flows.look_through_mix(client, MIX)
    iwda = _by_ticker(run)["IWDA.AS"]
    assert (iwda.kind, iwda.note) == (UNMAPPED, "holdings unavailable: couldn't reach data-store")
    # BRK-B has no snapshot to match against now.
    assert _by_ticker(run)["BRK-B"].matched_in is None
    assert run.exposures.snapshots == ()
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_a_snapshot_gone_since_the_fund_list_makes_the_fund_unmapped():
    run = flows.look_through_mix(_client(holdings={}), MIX)
    iwda = _by_ticker(run)["IWDA.AS"]
    assert (iwda.kind, iwda.note) == (UNMAPPED, "no holdings snapshot")


def test_an_etc_outside_the_instrument_table_is_unmapped():
    client = _client(
        info={"XAU.DE": EUR}, tracked=[_tracked("XAU.DE", "etc")], funds=(), holdings={}
    )
    run = flows.look_through_mix(client, [("XAU.DE", Decimal("1"))])
    xau = run.assets[0]
    assert (xau.kind, xau.note) == (UNMAPPED, "ETC not in portfolio's instrument table")


def test_a_hedged_fund_is_passed_on_as_hedged(monkeypatch):
    monkeypatch.setattr(flows, "HEDGED", {"IWDA.AS": "EUR"})
    run = flows.look_through_mix(_client(), [("IWDA.AS", Decimal("1"))])
    assert [b.label for b in run.exposures.currency.buckets] == ["hedged: IWDA.AS"]


def test_a_failed_tracked_tickers_call_fails_the_whole_request():
    # Without asset types, an ETF would be read as a stock: a wrong answer, not a partial one.
    with pytest.raises(DataStoreApiError):
        flows.look_through_mix(_client(tracked=DataStoreApiError("boom")), MIX)


def test_a_failed_fund_list_fails_the_whole_request():
    with pytest.raises(DataStoreApiError):
        flows.look_through_mix(_client(funds=DataStoreApiError("boom")), MIX)


def test_nothing_included_has_no_exposures():
    client = _client(info={"NOPE": UNKNOWN})
    run = flows.look_through_mix(client, [("NOPE", Decimal("1"))])
    assert run.exposures is None


def test_each_asset_and_fund_logs_its_outcome(caplog):
    with caplog.at_level(logging.INFO, logger="portfolio.look_through.flows"):
        flows.look_through_mix(_client(), MIX)
    messages = [r.getMessage() for r in caplog.records]
    assert messages[0] == "look-through: 5 asset(s) requested."
    assert "look-through: IWDA.AS: snapshot of world from 2026-08-30, 1 line(s)." in messages
    assert "look-through: IWDA.AS: looked through, weight 40.0%." in messages
    assert "look-through: BRK-B: direct, matched in IWDA.AS, weight 20.0%." in messages
    assert "look-through: EGLN.L: commodity (gold), weight 10.0%." in messages
    assert (
        "look-through: VWCE.DE: unmapped (no holdings data for this ETF), weight 20.0%." in messages
    )
