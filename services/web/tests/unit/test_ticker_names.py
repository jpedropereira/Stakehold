"""ticker_names (UX-1, UX-2): the {ticker: display_name} map a page shows its tickers' names
from. One unfiltered call per page; a failure leaves the page with bare tickers, never an error
page."""

import logging
from types import SimpleNamespace

import pytest
from web.portfolio_client.errors import PortfolioUnavailable
from web.ticker_names import load_names, load_tracked, names_from

pytestmark = pytest.mark.anyio


class FakePortfolioClient:
    def __init__(self, names: dict[str, str], error: Exception | None = None):
        self.names = names
        self.error = error
        self.calls: list[dict] = []

    async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
        self.calls.append({"requested_by": requested_by, "statuses": statuses, "tickers": tickers})
        if self.error:
            raise self.error
        return [
            SimpleNamespace(ticker=ticker, display_name=name, status="ready")
            for ticker, name in self.names.items()
        ]


async def test_returns_every_tracked_tickers_name():
    client = FakePortfolioClient({"VWCE.DE": "Vanguard FTSE All-World", "AAPL": "Apple Inc."})

    names = await load_names(client)

    assert names == {"VWCE.DE": "Vanguard FTSE All-World", "AAPL": "Apple Inc."}


async def test_leaves_out_a_name_that_only_repeats_its_ticker():
    client = FakePortfolioClient({"AAPL": "Apple Inc.", "XYZ": "XYZ", "QQQ": " QQQ "})

    names = await load_names(client)

    assert names == {"AAPL": "Apple Inc."}


async def test_leaves_out_an_empty_name():
    client = FakePortfolioClient({"AAPL": "Apple Inc.", "BLANK": "  "})

    names = await load_names(client)

    assert names == {"AAPL": "Apple Inc."}


async def test_a_ticker_data_store_doesnt_track_has_no_name():
    client = FakePortfolioClient({"AAPL": "Apple Inc."})

    names = await load_names(client)

    assert "IE00B4L5Y983" not in names


async def test_asks_once_for_every_tracked_ticker():
    """No ticker filter: a page's pickers offer every priced ticker, too many for one query
    string, and the tracked list (the only tickers with names) is small."""
    client = FakePortfolioClient({"AAPL": "Apple Inc."})

    await load_names(client)

    assert client.calls == [{"requested_by": None, "statuses": None, "tickers": None}]


async def test_a_failed_lookup_returns_no_names_and_says_so(caplog):
    client = FakePortfolioClient({"AAPL": "Apple Inc."}, error=PortfolioUnavailable("down"))

    with caplog.at_level(logging.WARNING, logger="web.ticker_names"):
        names = await load_names(client)

    assert names == {}
    assert "tracked tickers" in caplog.text
    assert "down" in caplog.text


async def test_an_unexpected_error_is_not_swallowed():
    """Only the client's own errors mean "the service is down"; a bug in web still surfaces."""
    client = FakePortfolioClient({}, error=TypeError("bug"))

    with pytest.raises(TypeError):
        await load_names(client)


async def test_load_tracked_returns_the_rows_for_a_page_to_reuse():
    """The holdings pages read names and statuses from the same rows: one call, not two."""
    client = FakePortfolioClient({"AAPL": "Apple Inc."})

    rows = await load_tracked(client)

    assert [(r.ticker, r.status) for r in rows] == [("AAPL", "ready")]
    assert names_from(rows) == {"AAPL": "Apple Inc."}


async def test_load_tracked_returns_no_rows_when_the_call_fails():
    client = FakePortfolioClient({"AAPL": "Apple Inc."}, error=PortfolioUnavailable("down"))

    assert await load_tracked(client) == []
