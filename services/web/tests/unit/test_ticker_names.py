"""ticker_names.load_names (UX-1): the {ticker: display_name} map a page shows its tickers' names
from. One call per page; a failure leaves the page with bare tickers, never an error page."""

import logging
from types import SimpleNamespace

import pytest
from web.portfolio_client.errors import PortfolioUnavailable
from web.ticker_names import load_names

pytestmark = pytest.mark.anyio


class FakePortfolioClient:
    def __init__(self, names: dict[str, str], error: Exception | None = None):
        self.names = names
        self.error = error
        self.calls: list[list[str]] = []

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.calls.append(list(tickers or ()))
        if self.error:
            raise self.error
        return [
            SimpleNamespace(ticker=ticker, display_name=self.names[ticker])
            for ticker in tickers or ()
            if ticker in self.names
        ]


async def test_returns_each_tickers_name():
    client = FakePortfolioClient({"VWCE.DE": "Vanguard FTSE All-World", "AAPL": "Apple Inc."})

    names = await load_names(client, ["VWCE.DE", "AAPL"])

    assert names == {"VWCE.DE": "Vanguard FTSE All-World", "AAPL": "Apple Inc."}


async def test_leaves_out_a_name_that_only_repeats_its_ticker():
    client = FakePortfolioClient({"AAPL": "Apple Inc.", "XYZ": "XYZ", "QQQ": " QQQ "})

    names = await load_names(client, ["AAPL", "XYZ", "QQQ"])

    assert names == {"AAPL": "Apple Inc."}


async def test_leaves_out_an_empty_name():
    client = FakePortfolioClient({"AAPL": "Apple Inc.", "BLANK": "  "})

    names = await load_names(client, ["AAPL", "BLANK"])

    assert names == {"AAPL": "Apple Inc."}


async def test_a_ticker_data_store_doesnt_track_has_no_name():
    client = FakePortfolioClient({"AAPL": "Apple Inc."})

    names = await load_names(client, ["AAPL", "IE00B4L5Y983"])

    assert names == {"AAPL": "Apple Inc."}


async def test_asks_once_with_each_ticker_once_in_sorted_order():
    client = FakePortfolioClient({"AAPL": "Apple Inc."})

    await load_names(client, ["VWCE.DE", "AAPL", "VWCE.DE"])

    assert client.calls == [["AAPL", "VWCE.DE"]]


async def test_no_tickers_means_no_call():
    client = FakePortfolioClient({})

    names = await load_names(client, [])

    assert names == {}
    assert client.calls == []


async def test_a_failed_lookup_returns_no_names_and_says_so(caplog):
    client = FakePortfolioClient({"AAPL": "Apple Inc."}, error=PortfolioUnavailable("down"))

    with caplog.at_level(logging.WARNING, logger="web.ticker_names"):
        names = await load_names(client, ["AAPL"])

    assert names == {}
    assert "ticker names" in caplog.text
    assert "down" in caplog.text


async def test_an_unexpected_error_is_not_swallowed():
    """Only the client's own errors mean "the service is down"; a bug in web still surfaces."""
    client = FakePortfolioClient({}, error=TypeError("bug"))

    with pytest.raises(TypeError):
        await load_names(client, ["AAPL"])
