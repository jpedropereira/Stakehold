from datetime import date
from decimal import Decimal

import pytest
from data_store.config_loader import CurrenciesConfig, CurrencyConfig
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
    def __init__(self, status=None):
        self.status = status

    def get_tracked_ticker(self, conn, ticker, source):
        if self.status is None:
            return None
        from types import SimpleNamespace

        return SimpleNamespace(status=self.status)


@pytest.fixture(autouse=True)
def untracked(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(read_flows, "tracked_tickers_repository", FakeTrackedRepo())


def test_ticker_info_not_tracked(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows, "returns_repository", FakeReturnsRepo(exists=False, date_range=(None, None))
    )

    result = ticker_info(None, "NOPE", "yfinance")

    assert result == {
        "ticker": "NOPE",
        "exists": False,
        "has_returns": False,
        "first_date": None,
        "last_date": None,
        "status": None,
    }


def test_ticker_info_reports_the_onboarding_status_of_a_tracked_ticker(monkeypatch):
    # A ticker can be tracked before it has a single stored row: that's what "pending" is.
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows, "returns_repository", FakeReturnsRepo(exists=False, date_range=(None, None))
    )
    monkeypatch.setattr(read_flows, "tracked_tickers_repository", FakeTrackedRepo("pending"))

    result = ticker_info(None, "ASML.AS", "yfinance")

    assert (result["exists"], result["status"]) == (False, "pending")


def test_ticker_info_tracked_without_returns_computed_yet(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows, "returns_repository", FakeReturnsRepo(exists=True, date_range=(None, None))
    )

    result = ticker_info(None, "AAPL", "yfinance")

    assert result["exists"] is True
    assert result["has_returns"] is False


def test_ticker_info_tracked_with_returns(monkeypatch):
    import data_store.read_flows as read_flows

    monkeypatch.setattr(
        read_flows,
        "returns_repository",
        FakeReturnsRepo(exists=True, date_range=(date(2010, 1, 4), date(2026, 8, 29))),
    )

    result = ticker_info(None, "AAPL", "yfinance")

    assert result["has_returns"] is True
    assert result["first_date"] == date(2010, 1, 4)
    assert result["last_date"] == date(2026, 8, 29)


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
