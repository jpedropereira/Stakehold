from datetime import date
from decimal import Decimal

import pytest
from data_store.config_loader import CurrenciesConfig, CurrencyConfig, TickerResolution
from data_store.read_flows import (
    CurrencyNotTracked,
    TickerNotFound,
    fx_rate_series,
    price_series,
    ticker_info,
    ticker_list,
)


class FakeReturnsRepo:
    def __init__(self, *, exists=True, date_range=(None, None), series=None):
        self._exists = exists
        self._date_range = date_range
        self._series = series or []

    def ticker_exists(self, conn, ticker, source):
        return self._exists

    def list_tickers(self, conn, source):
        return ["AAPL", "IWDA.AS"] if source == "yfinance" else []

    def get_returns_date_range(self, conn, ticker, source):
        return self._date_range

    def get_return_series(self, conn, ticker, source, start, end):
        return self._series


class FakeFxRepo:
    def __init__(self, *, series_by_pair=None, dates_by_currency=None):
        self._series_by_pair = series_by_pair or {}
        self._dates_by_currency = dates_by_currency or {}

    def get_rate_series(self, conn, base, quote, source, start, end):
        return self._series_by_pair.get((base, quote), [])

    def get_dates_with_rate(self, conn, currency, source, start, end):
        return self._dates_by_currency.get(currency, [])


def make_currencies(*, base="EUR", codes=("EUR", "USD", "GBP", "CHF"), disabled=()):
    return CurrenciesConfig(
        base_currency=base,
        currencies=[CurrencyConfig(code=c, enabled=c not in disabled) for c in codes],
    )


# --- price_series ------------------------------------------------------------------------------


def test_price_series_raises_ticker_not_found_when_ticker_never_ingested(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "returns_repository", FakeReturnsRepo(exists=False))

    with pytest.raises(TickerNotFound):
        price_series(None, "NOPE", "yfinance", date(2024, 1, 1), date(2024, 1, 31))


def test_price_series_returns_points_verbatim_including_nulls(monkeypatch):
    import data_store.read_flows as read_flows

    row = (date(2024, 1, 2), Decimal("100.0"), Decimal("1.0"), None, None, None, None)
    monkeypatch.setattr(
        read_flows, "returns_repository", FakeReturnsRepo(exists=True, series=[row])
    )

    result = price_series(None, "AAPL", "yfinance", date(2024, 1, 1), date(2024, 1, 31))

    assert result["ticker"] == "AAPL"
    assert result["source"] == "yfinance"
    point = result["points"][0]
    assert point["date"] == date(2024, 1, 2)
    assert point["split_adjusted_close"] == Decimal("100.0")
    assert point["price_return"] is None


def test_price_series_empty_range_returns_empty_points_not_an_error(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "returns_repository", FakeReturnsRepo(exists=True, series=[]))

    result = price_series(None, "AAPL", "yfinance", date(2024, 1, 1), date(2024, 1, 31))

    assert result["points"] == []


# --- ticker_info ---------------------------------------------------------------------------


class FakeTrackedRepo:
    def __init__(self, status=None, currency=None):
        self.status = status
        self.currency = currency

    def get_tracked_ticker(self, conn, ticker, source):
        if self.status is None:
            return None
        from types import SimpleNamespace

        return SimpleNamespace(status=self.status, currency=self.currency)


class FakeHoldingsRepo:
    """`listings` is what get_listing_currencies returns: (raw ticker, exchange, currency),
    newest snapshot first."""

    def __init__(self, listings=()):
        self.listings = list(listings)
        self.calls = 0

    def get_listing_currencies(self, conn):
        self.calls += 1
        return self.listings


RESOLUTION = TickerResolution(
    exchange_suffixes={
        "Hong Kong Exchanges And Clearing Ltd": ".HK",
        "Nyse Euronext - Euronext Paris": ".PA",
        "New York Stock Exchange Inc.": None,
        "London Stock Exchange": ".L",
    },
    ticker_overrides={"BRKB": "BRK-B"},
)


@pytest.fixture(autouse=True)
def untracked(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "tracked_tickers_repository", FakeTrackedRepo())
    monkeypatch.setattr(read_flows, "holdings_repository", FakeHoldingsRepo())


@pytest.fixture
def priced(monkeypatch):
    """A ticker with stored history, so the currency tests below only vary the currency."""
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "returns_repository",
        FakeReturnsRepo(exists=True, date_range=(date(2010, 1, 4), date(2026, 8, 29))),
    )


def test_ticker_info_not_tracked(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows, "returns_repository", FakeReturnsRepo(exists=False, date_range=(None, None))
    )

    result = ticker_info(None, "NOPE", "yfinance", RESOLUTION)

    assert result == {
        "ticker": "NOPE",
        "exists": False,
        "has_returns": False,
        "first_date": None,
        "last_date": None,
        "status": None,
        "currency": None,
        "fx_currency": None,
    }


def test_ticker_info_reports_the_onboarding_status_of_a_tracked_ticker(monkeypatch):
    # A ticker can be tracked before it has a single stored row: that's what "pending" is.
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows, "returns_repository", FakeReturnsRepo(exists=False, date_range=(None, None))
    )
    monkeypatch.setattr(read_flows, "tracked_tickers_repository", FakeTrackedRepo("pending"))

    result = ticker_info(None, "ASML.AS", "yfinance", RESOLUTION)

    assert (result["exists"], result["status"]) == (False, "pending")


def test_ticker_info_tracked_without_returns_computed_yet(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows, "returns_repository", FakeReturnsRepo(exists=True, date_range=(None, None))
    )

    result = ticker_info(None, "AAPL", "yfinance", RESOLUTION)

    assert result["exists"] is True
    assert result["has_returns"] is False


def test_ticker_info_tracked_with_returns(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "returns_repository",
        FakeReturnsRepo(exists=True, date_range=(date(2010, 1, 4), date(2026, 8, 29))),
    )

    result = ticker_info(None, "AAPL", "yfinance", RESOLUTION)

    assert result["has_returns"] is True
    assert result["first_date"] == date(2010, 1, 4)
    assert result["last_date"] == date(2026, 8, 29)


# --- ticker_info: currency -----------------------------------------------------------------


def test_ticker_info_takes_a_tracked_tickers_currency_verbatim(monkeypatch, priced):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "tracked_tickers_repository", FakeTrackedRepo("ready", "GBp"))
    holdings = FakeHoldingsRepo([("VOD", "London Stock Exchange", "GBP")])
    monkeypatch.setattr(read_flows, "holdings_repository", holdings)

    result = ticker_info(None, "VOD.L", "yfinance", RESOLUTION)

    # Verbatim: a pence-quoted listing stays GBp. fx_currency is the pair FX rates exist for.
    assert (result["currency"], result["fx_currency"]) == ("GBp", "GBP")
    assert holdings.calls == 0


def test_ticker_info_falls_back_to_holdings_when_the_tracked_currency_is_null(monkeypatch, priced):
    # A config row has no currency until onboarding's lookup fills it in.
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "tracked_tickers_repository", FakeTrackedRepo("pending", None))
    monkeypatch.setattr(
        read_flows,
        "holdings_repository",
        FakeHoldingsRepo([("AAPL", "New York Stock Exchange Inc.", "USD")]),
    )

    result = ticker_info(None, "AAPL", "yfinance", RESOLUTION)

    assert (result["currency"], result["fx_currency"]) == ("USD", "USD")


def test_ticker_info_resolves_a_holdings_only_ticker_through_the_suffix_map(monkeypatch, priced):
    # Stored raw as "700"; priced as "0700.HK" after Hong Kong's zero-padding.
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "holdings_repository",
        FakeHoldingsRepo([("700", "Hong Kong Exchanges And Clearing Ltd", "HKD")]),
    )

    result = ticker_info(None, "0700.HK", "yfinance", RESOLUTION)

    assert (result["currency"], result["fx_currency"]) == ("HKD", "HKD")


@pytest.mark.parametrize("ticker,expected", [("DG.PA", "EUR"), ("DG", "USD")])
def test_ticker_info_tells_apart_one_raw_symbol_on_two_exchanges(
    monkeypatch, priced, ticker, expected
):
    # Real case: iShares lists Vinci (Paris) and Dollar General (NYSE) both as "DG".
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "holdings_repository",
        FakeHoldingsRepo(
            [
                ("DG", "Nyse Euronext - Euronext Paris", "EUR"),
                ("DG", "New York Stock Exchange Inc.", "USD"),
            ]
        ),
    )

    result = ticker_info(None, ticker, "yfinance", RESOLUTION)

    assert result["currency"] == expected


def test_ticker_info_resolves_a_holding_through_a_ticker_override(monkeypatch, priced):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "holdings_repository",
        FakeHoldingsRepo([("BRKB", "New York Stock Exchange Inc.", "USD")]),
    )

    result = ticker_info(None, "BRK-B", "yfinance", RESOLUTION)

    assert result["currency"] == "USD"


def test_ticker_info_takes_the_newest_holding_when_two_resolve_to_one_ticker(monkeypatch, priced):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "holdings_repository",
        FakeHoldingsRepo(
            [
                ("700", "Hong Kong Exchanges And Clearing Ltd", "HKD"),
                ("0700", "Hong Kong Exchanges And Clearing Ltd", "USD"),
            ]
        ),
    )

    result = ticker_info(None, "0700.HK", "yfinance", RESOLUTION)

    assert result["currency"] == "HKD"


def test_ticker_info_ignores_placeholder_and_unmapped_holdings(monkeypatch, priced):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "holdings_repository",
        FakeHoldingsRepo(
            [
                ("-", "London Stock Exchange", "GBP"),
                ("ACME", "Some Unmapped Exchange", "JPY"),
            ]
        ),
    )

    assert ticker_info(None, "-.L", "yfinance", RESOLUTION)["currency"] is None
    assert ticker_info(None, "ACME", "yfinance", RESOLUTION)["currency"] is None


def test_ticker_info_currency_is_null_when_no_source_knows_it(monkeypatch, priced):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "holdings_repository",
        FakeHoldingsRepo([("MSFT", "New York Stock Exchange Inc.", "USD")]),
    )

    result = ticker_info(None, "SAP.DE", "yfinance", RESOLUTION)

    assert (result["currency"], result["fx_currency"]) == (None, None)


def test_ticker_info_fx_currency_is_null_for_a_currency_with_no_iso_code(monkeypatch, priced):
    # major_currency's rule: only a 3-letter ISO code can pick an FX pair.
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "tracked_tickers_repository", FakeTrackedRepo("ready", "XX"))

    result = ticker_info(None, "ODD", "yfinance", RESOLUTION)

    assert (result["currency"], result["fx_currency"]) == ("XX", None)


# --- fx_rate_series: currency tracking -----------------------------------------------------


def test_fx_rate_series_raises_when_base_not_tracked(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "fx_repository", FakeFxRepo())

    with pytest.raises(CurrencyNotTracked):
        fx_rate_series(
            None, make_currencies(), "JPY", "USD", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
        )


def test_fx_rate_series_raises_when_quote_not_tracked(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "fx_repository", FakeFxRepo())

    with pytest.raises(CurrencyNotTracked):
        fx_rate_series(
            None, make_currencies(), "EUR", "JPY", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
        )


def test_fx_rate_series_raises_when_currency_disabled(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "fx_repository", FakeFxRepo())

    with pytest.raises(CurrencyNotTracked):
        fx_rate_series(
            None,
            make_currencies(disabled=("CHF",)),
            "EUR",
            "CHF",
            "yfinance",
            date(2024, 1, 1),
            date(2024, 1, 31),
        )


# --- fx_rate_series: direct/inverse/identity/triangulated ---------------------------------


def test_fx_rate_series_direct_when_base_is_the_configured_base(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(series_by_pair={("EUR", "USD"): [(date(2024, 1, 2), Decimal("1.10"))]}),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "EUR", "USD", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert result["derivation"] == "direct"
    assert result["via"] is None
    assert result["points"] == [{"date": date(2024, 1, 2), "rate": Decimal("1.10")}]


def test_fx_rate_series_inverse_when_quote_is_the_configured_base(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(series_by_pair={("EUR", "USD"): [(date(2024, 1, 2), Decimal("2"))]}),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "USD", "EUR", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert result["derivation"] == "inverse"
    assert result["points"] == [{"date": date(2024, 1, 2), "rate": Decimal("0.5")}]


def test_fx_rate_series_identity_reports_rate_one_for_every_known_date(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(dates_by_currency={"USD": [date(2024, 1, 2), date(2024, 1, 3)]}),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "USD", "USD", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert result["derivation"] == "identity"
    assert result["points"] == [
        {"date": date(2024, 1, 2), "rate": Decimal(1)},
        {"date": date(2024, 1, 3), "rate": Decimal(1)},
    ]


def test_fx_rate_series_triangulates_through_the_configured_base(monkeypatch):
    """rate(GBP, CHF) = rate(EUR, CHF) / rate(EUR, GBP) -- schema.md's documented cross-rate
    formula, with EUR as the configured base."""
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(
            series_by_pair={
                ("EUR", "CHF"): [(date(2024, 1, 2), Decimal("0.95"))],
                ("EUR", "GBP"): [(date(2024, 1, 2), Decimal("0.85"))],
            }
        ),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "GBP", "CHF", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert result["derivation"] == "triangulated"
    assert result["via"] == "EUR"
    # Rounded to fx_rates.rate's own NUMERIC(18,8) scale, not the Decimal context's 28 digits.
    assert result["points"] == [{"date": date(2024, 1, 2), "rate": Decimal("1.11764706")}]


def test_fx_rate_series_triangulation_only_keeps_shared_dates(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(
            series_by_pair={
                ("EUR", "CHF"): [
                    (date(2024, 1, 2), Decimal("0.95")),
                    (date(2024, 1, 3), Decimal("0.96")),
                ],
                ("EUR", "GBP"): [(date(2024, 1, 2), Decimal("0.85"))],
            }
        ),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "GBP", "CHF", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert [p["date"] for p in result["points"]] == [date(2024, 1, 2)]


# --- fx_rate_series: corrupt rates and derived-rate precision -------------------------------


def test_fx_rate_series_inverse_skips_a_zero_rate_instead_of_failing_the_range(monkeypatch):
    """Ingestion only rejects rates *above* the column's range, so a 0 can be stored. Dividing
    by it used to 500 the whole request, while the direct pair for the same day returned 200."""
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(
            series_by_pair={
                ("EUR", "USD"): [
                    (date(2024, 1, 2), Decimal("0")),
                    (date(2024, 1, 3), Decimal("2")),
                ]
            }
        ),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "USD", "EUR", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert result["points"] == [{"date": date(2024, 1, 3), "rate": Decimal("0.5")}]


def test_fx_rate_series_triangulation_skips_a_zero_denominator(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(
            series_by_pair={
                ("EUR", "CHF"): [
                    (date(2024, 1, 2), Decimal("0.95")),
                    (date(2024, 1, 3), Decimal("0.96")),
                ],
                ("EUR", "GBP"): [
                    (date(2024, 1, 2), Decimal("0")),
                    (date(2024, 1, 3), Decimal("0.85")),
                ],
            }
        ),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "GBP", "CHF", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert [p["date"] for p in result["points"]] == [date(2024, 1, 3)]


def test_fx_rate_series_direct_skips_a_nonsense_rate_too(monkeypatch):
    """Same rate, same day: a pair must not be usable in one derivation and corrupt in another."""
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(
            series_by_pair={
                ("EUR", "USD"): [
                    (date(2024, 1, 2), Decimal("0")),
                    (date(2024, 1, 3), Decimal("1.10")),
                ]
            }
        ),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "EUR", "USD", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert result["points"] == [{"date": date(2024, 1, 3), "rate": Decimal("1.10")}]


def test_fx_rate_series_warns_about_a_skipped_rate_rather_than_dropping_it_silently(
    monkeypatch, caplog
):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(series_by_pair={("EUR", "USD"): [(date(2024, 1, 2), Decimal("0"))]}),
    )

    with caplog.at_level("WARNING"):
        read_flows.fx_rate_series(
            None, make_currencies(), "USD", "EUR", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
        )

    assert "2024-01-02" in caplog.text


def test_fx_rate_series_derived_rates_carry_the_stored_columns_scale(monkeypatch):
    """A derived rate is a `Decimal` division, which uses the default 28-digit context -- so
    /fx/GBP/CHF returned 28 significant digits while a stored pair returned the column's 8dp."""
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(
            series_by_pair={
                ("EUR", "CHF"): [(date(2024, 1, 2), Decimal("0.95000000"))],
                ("EUR", "GBP"): [(date(2024, 1, 2), Decimal("0.85000000"))],
            }
        ),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "GBP", "CHF", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert str(result["points"][0]["rate"]) == "1.11764706"


def test_fx_rate_series_inverse_rates_carry_the_stored_columns_scale(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "fx_repository",
        FakeFxRepo(series_by_pair={("EUR", "USD"): [(date(2024, 1, 2), Decimal("3"))]}),
    )

    result = read_flows.fx_rate_series(
        None, make_currencies(), "USD", "EUR", "yfinance", date(2024, 1, 1), date(2024, 1, 31)
    )

    assert str(result["points"][0]["rate"]) == "0.33333333"


# --- ticker_list ---------------------------------------------------------------------------


def test_ticker_list_wraps_the_repositorys_tickers(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "returns_repository", FakeReturnsRepo())

    assert ticker_list(None, "yfinance") == {"tickers": ["AAPL", "IWDA.AS"]}


def test_ticker_list_is_empty_for_a_source_with_nothing_ingested(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "returns_repository", FakeReturnsRepo())

    assert ticker_list(None, "other") == {"tickers": []}
