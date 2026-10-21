from datetime import date
from decimal import Decimal

import pytest
from data_store.config_loader import CurrenciesConfig, CurrencyConfig, TickerResolution
from data_store.read_flows import (
    CurrencyNotTracked,
    TickerNotFound,
    fx_rate_series,
    price_series,
    regimes_overlapping,
    risk_free_series,
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


# --- risk_free_series ---------------------------------------------------------------------


class FakeRiskFreeRepo:
    """Stored rows per series; reads honour the requested range as the real query does."""

    def __init__(self, rows_by_series=None):
        self._rows = rows_by_series or {}
        self.series_reads = []

    def get_risk_free_rate_series(self, conn, source, series, start, end):
        self.series_reads.append((source, series, start, end))
        return [(d, rate) for d, rate in self._rows.get(series, []) if start <= d <= end]

    def get_first_risk_free_dates(self, conn, source, series):
        return {s: min(d for d, _ in self._rows[s]) for s in series if self._rows.get(s)}


_STORED_RATES = {
    "eonia": [
        (date(1999, 1, 4), Decimal("3.2000")),
        (date(2019, 9, 30), Decimal("-0.4680")),
        (date(2019, 10, 1), Decimal("-0.4640")),
    ],
    "estr": [(date(2019, 10, 1), Decimal("-0.5490")), (date(2019, 10, 2), Decimal("-0.5500"))],
}


def _with_rates(monkeypatch, rows_by_series):
    import data_store.read_flows as read_flows

    repo = FakeRiskFreeRepo(rows_by_series)
    monkeypatch.setattr(read_flows, "risk_free_repository", repo)
    return repo


def test_risk_free_series_splices_across_the_cutoff(monkeypatch):
    _with_rates(monkeypatch, _STORED_RATES)

    result = risk_free_series(None, "ecb", date(2019, 9, 27), date(2019, 10, 4))

    assert result == {
        "source": "ecb",
        "first_date": date(1999, 1, 4),
        "points": [
            {"date": date(2019, 9, 30), "rate": Decimal("-0.5530"), "series": "eonia_adjusted"},
            {"date": date(2019, 10, 1), "rate": Decimal("-0.5490"), "series": "estr"},
            {"date": date(2019, 10, 2), "rate": Decimal("-0.5500"), "series": "estr"},
        ],
    }


def test_risk_free_series_reads_both_series_for_the_requested_source(monkeypatch):
    repo = _with_rates(monkeypatch, _STORED_RATES)

    risk_free_series(None, "test", date(2019, 9, 27), date(2019, 10, 4))

    assert sorted(repo.series_reads) == [
        ("test", "eonia", date(2019, 9, 27), date(2019, 10, 4)),
        ("test", "estr", date(2019, 9, 27), date(2019, 10, 4)),
    ]


def test_risk_free_series_before_1999_is_empty_but_still_names_where_coverage_begins(
    monkeypatch, caplog
):
    _with_rates(monkeypatch, _STORED_RATES)
    caplog.set_level("INFO")

    result = risk_free_series(None, "ecb", date(1998, 1, 1), date(1998, 12, 31))

    assert result["points"] == []
    assert result["first_date"] == date(1999, 1, 4)
    assert "no published days in range" in caplog.text


def test_risk_free_series_window_straddling_1999_starts_on_the_first_published_day(monkeypatch):
    _with_rates(monkeypatch, _STORED_RATES)

    result = risk_free_series(None, "ecb", date(1998, 12, 1), date(1999, 1, 10))

    assert [p["date"] for p in result["points"]] == [date(1999, 1, 4)]
    assert result["points"][0]["rate"] == Decimal("3.1150")


def test_risk_free_series_with_nothing_stored_says_so_apart_from_an_empty_window(
    monkeypatch, caplog
):
    _with_rates(monkeypatch, {})
    caplog.set_level("INFO")

    result = risk_free_series(None, "ecb", date(2024, 1, 1), date(2024, 1, 31))

    assert result == {"source": "ecb", "first_date": None, "points": []}
    assert "nothing stored" in caplog.text
    assert "no published days in range" not in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_risk_free_series_logs_counts_per_series(monkeypatch, caplog):
    _with_rates(monkeypatch, _STORED_RATES)
    caplog.set_level("INFO")

    risk_free_series(None, "ecb", date(2019, 9, 27), date(2019, 10, 4))

    assert "3 point(s)" in caplog.text
    assert "eonia_adjusted=1" in caplog.text
    assert "estr=2" in caplog.text
    assert "first_date=1999-01-04" in caplog.text


# --- regimes_overlapping ------------------------------------------------------------------------


class FakeRegimesRepo:
    def __init__(self, *, rows=(), total=0):
        self._rows = list(rows)
        self._total = total
        self.reads = []

    def get_overlapping_regimes(self, conn, start, end):
        self.reads.append((start, end))
        return self._rows

    def count_regimes(self, conn):
        return self._total


def _with_regimes(monkeypatch, **kwargs):
    import data_store.read_flows as read_flows

    repo = FakeRegimesRepo(**kwargs)
    monkeypatch.setattr(read_flows, "regimes_repository", repo)
    return repo


_COVID_CRASH = (
    "spx_bear_2020",
    "drawdown",
    "SPX",
    "COVID crash",
    date(2020, 2, 19),
    date(2020, 3, 23),
    date(2020, 3, 23),
    "severe",
    "manual_approx",
    "about -34%",
)
_ONGOING = ("ongoing", "inflation", "EA", "Ongoing", date(2022, 1, 1), None, None, None, "x", None)


def test_regimes_overlapping_maps_rows_verbatim_keeping_nulls(monkeypatch):
    repo = _with_regimes(monkeypatch, rows=[_COVID_CRASH, _ONGOING], total=93)

    result = regimes_overlapping(None, date(2020, 1, 1), date(2024, 12, 31))

    assert repo.reads == [(date(2020, 1, 1), date(2024, 12, 31))]
    assert result == {
        "total_regimes": 93,
        "regimes": [
            {
                "regime_id": "spx_bear_2020",
                "dimension": "drawdown",
                "scope": "SPX",
                "name": "COVID crash",
                "start_date": date(2020, 2, 19),
                "end_date": date(2020, 3, 23),
                "known_from": date(2020, 3, 23),
                "severity": "severe",
                "source": "manual_approx",
                "notes": "about -34%",
            },
            {
                "regime_id": "ongoing",
                "dimension": "inflation",
                "scope": "EA",
                "name": "Ongoing",
                "start_date": date(2022, 1, 1),
                "end_date": None,
                "known_from": None,
                "severity": None,
                "source": "x",
                "notes": None,
            },
        ],
    }


def test_regimes_overlapping_logs_the_count_against_the_total(monkeypatch, caplog):
    _with_regimes(monkeypatch, rows=[_COVID_CRASH], total=93)
    caplog.set_level("INFO")

    regimes_overlapping(None, date(2020, 1, 1), date(2020, 12, 31))

    assert "1 overlapping 2020-01-01..2020-12-31 (of 93 stored)" in caplog.text
    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_regimes_overlapping_an_empty_range_is_not_an_empty_table(monkeypatch, caplog):
    _with_regimes(monkeypatch, rows=[], total=93)
    caplog.set_level("INFO")

    result = regimes_overlapping(None, date(1900, 1, 1), date(1900, 12, 31))

    assert result == {"total_regimes": 93, "regimes": []}
    assert "none overlapping 1900-01-01..1900-12-31 (of 93 stored)" in caplog.text
    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_regimes_overlapping_warns_when_nothing_is_stored(monkeypatch, caplog):
    _with_regimes(monkeypatch, rows=[], total=0)
    caplog.set_level("INFO")

    result = regimes_overlapping(None, date(2020, 1, 1), date(2020, 12, 31))

    assert result == {"total_regimes": 0, "regimes": []}
    assert "none stored" in caplog.text
    assert "none overlapping" not in caplog.text
    assert any(r.levelname == "WARNING" for r in caplog.records)
