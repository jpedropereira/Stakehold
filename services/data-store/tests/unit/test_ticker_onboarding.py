"""sync_config_tickers: tracked_tickers.yaml -> the tracked_tickers table.

The repository is replaced by an in-memory stand-in, so these tests pin down the sync's decisions
(what gets inserted, updated, left alone or warned about) rather than its SQL, which
tests/integration/test_db_tracked_tickers.py covers against a real Postgres.
"""

import logging
from datetime import datetime, timezone

import pytest
from data_store import ticker_onboarding
from data_store.config_loader import CurrenciesConfig, CurrencyConfig, TickerConfig
from data_store.tracked_tickers.base import (
    OUTCOME_FOUND,
    OUTCOME_NOT_FOUND,
    OUTCOME_UNAVAILABLE,
    OUTCOME_UNSUPPORTED,
    LookupResult,
)
from data_store.tracked_tickers.repository import TrackedTicker

_NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def config_ticker(ticker, *, display_name=None, asset_type="etf", enabled=True):
    return TickerConfig(
        ticker=ticker,
        display_name=display_name or ticker,
        asset_type=asset_type,
        price_source="yfinance",
        enabled=enabled,
    )


def stored(ticker, *, origin="config", display_name=None, asset_type="etf", enabled=True):
    return TrackedTicker(
        ticker=ticker,
        source="yfinance",
        display_name=display_name or ticker,
        asset_type=asset_type,
        currency=None,
        exchange=None,
        origin=origin,
        status="ready",
        enabled=enabled,
        attempts=0,
        next_attempt_at=None,
        last_error=None,
        requested_by=None,
        requested_at=_NOW,
        ready_at=_NOW,
        updated_at=_NOW,
    )


class FakeRepository:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.saved = None

    def get_tracked_tickers(self, conn):
        return list(self.rows)

    def save_config_tickers(self, conn, *, inserts, updates):
        self.saved = {"inserts": list(inserts), "updates": list(updates)}
        return len(inserts), len(updates)


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepository()
    for name in ("get_tracked_tickers", "save_config_tickers"):
        monkeypatch.setattr(ticker_onboarding.tracked_tickers_repository, name, getattr(fake, name))
    return fake


def test_inserts_yaml_tickers_the_table_does_not_have(repo):
    result = ticker_onboarding.sync_config_tickers(None, [config_ticker("IWDA.AS")])

    assert [t.ticker for t in repo.saved["inserts"]] == ["IWDA.AS"]
    assert repo.saved["updates"] == []
    assert result["status"] == "ok"
    assert result["rows_saved"] == 1
    assert result["tickers_added"] == 1


def test_inserts_a_disabled_yaml_ticker_too(repo):
    # The row keeps its enabled flag; nothing onboards or prices a disabled row.
    ticker_onboarding.sync_config_tickers(None, [config_ticker("OLD.AS", enabled=False)])

    assert [(t.ticker, t.enabled) for t in repo.saved["inserts"]] == [("OLD.AS", False)]


def test_updates_a_config_row_whose_yaml_entry_changed(repo):
    repo.rows = [stored("IWDA.AS", display_name="Old name", enabled=True)]

    result = ticker_onboarding.sync_config_tickers(
        None, [config_ticker("IWDA.AS", display_name="New name", enabled=False)]
    )

    assert repo.saved["inserts"] == []
    assert [(t.display_name, t.enabled) for t in repo.saved["updates"]] == [("New name", False)]
    assert result["tickers_updated"] == 1
    assert result["rows_saved"] == 1


def test_an_asset_type_change_is_an_update(repo):
    repo.rows = [stored("8PSG.DE", asset_type="etf")]

    ticker_onboarding.sync_config_tickers(None, [config_ticker("8PSG.DE", asset_type="etc")])

    assert [t.asset_type for t in repo.saved["updates"]] == ["etc"]


def test_an_unchanged_yaml_writes_nothing_and_says_so(repo, caplog):
    caplog.set_level(logging.INFO)
    repo.rows = [stored("IWDA.AS")]

    result = ticker_onboarding.sync_config_tickers(None, [config_ticker("IWDA.AS")])

    assert repo.saved is None
    assert result["status"] == "ok"
    assert result["rows_saved"] == 0
    assert "already up to date" in caplog.text


def test_a_yaml_entry_takes_over_a_user_requested_row(repo):
    # The operator's file is authoritative for every ticker it names, so naming a ticker a user
    # requested first turns it into a config row the YAML now owns.
    repo.rows = [stored("ASML.AS", origin="request", asset_type="equity")]

    result = ticker_onboarding.sync_config_tickers(
        None, [config_ticker("ASML.AS", asset_type="equity")]
    )

    assert [t.ticker for t in repo.saved["updates"]] == ["ASML.AS"]
    assert result["tickers_updated"] == 1


def test_a_config_row_missing_from_the_yaml_is_warned_about_and_left_alone(repo, caplog):
    repo.rows = [stored("IWDA.AS"), stored("GONE.AS")]

    result = ticker_onboarding.sync_config_tickers(None, [config_ticker("IWDA.AS")])

    assert repo.saved is None
    assert result["tickers_orphaned"] == 1
    assert result["status"] == "ok"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "GONE.AS" in warnings[0].getMessage()


def test_a_requested_row_missing_from_the_yaml_is_not_an_orphan(repo, caplog):
    repo.rows = [stored("ASML.AS", origin="request")]

    result = ticker_onboarding.sync_config_tickers(None, [])

    assert result["tickers_orphaned"] == 0
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_an_empty_yaml_is_nothing_to_do(repo, caplog):
    caplog.set_level(logging.INFO)

    result = ticker_onboarding.sync_config_tickers(None, [])

    assert result["status"] == "nothing_to_do"
    assert result["rows_saved"] == 0
    assert "no tickers in tracked_tickers.yaml" in caplog.text


def test_a_ticker_too_long_for_the_column_is_skipped_and_fails_the_run(repo, caplog):
    too_long = "X" * 21

    result = ticker_onboarding.sync_config_tickers(
        None, [config_ticker(too_long), config_ticker("IWDA.AS")]
    )

    assert [t.ticker for t in repo.saved["inserts"]] == ["IWDA.AS"]
    assert result["status"] == "partial"
    assert result["tickers_skipped"] == 1
    assert too_long in caplog.text


def test_logs_scope_and_summary(repo, caplog):
    caplog.set_level(logging.INFO)
    repo.rows = [stored("VWCE.DE", display_name="Old")]

    ticker_onboarding.sync_config_tickers(
        None, [config_ticker("IWDA.AS"), config_ticker("VWCE.DE", display_name="New")]
    )

    assert "sync-tracked-tickers: 2 ticker(s) in tracked_tickers.yaml" in caplog.text
    assert "1 added, 1 updated, 0 skipped, 0 orphaned" in caplog.text


# --- tracked_ticker_configs ----------------------------------------------------------------------


def with_status(row, status, *, enabled=True):
    from dataclasses import replace

    return replace(row, status=status, enabled=enabled)


def test_ready_only_returns_enabled_ready_rows_as_ticker_configs(repo):
    repo.rows = [
        with_status(stored("IWDA.AS", display_name="iShares World"), "ready"),
        with_status(stored("NEW.AS"), "pending"),
        with_status(stored("BUSY.AS"), "backfilling"),
        with_status(stored("BAD.AS"), "failed"),
        with_status(stored("OFF.AS"), "ready", enabled=False),
    ]

    configs = ticker_onboarding.tracked_ticker_configs(None, ready_only=True)

    assert configs == [
        TickerConfig(
            ticker="IWDA.AS",
            display_name="iShares World",
            asset_type="etf",
            price_source="yfinance",
            enabled=True,
        )
    ]


def test_without_ready_only_every_enabled_row_is_returned(repo):
    # Backfill must reach a ticker that isn't ready yet: filling its history is what makes it
    # ready.
    repo.rows = [
        with_status(stored("IWDA.AS"), "ready"),
        with_status(stored("NEW.AS"), "pending"),
        with_status(stored("BAD.AS"), "failed"),
        with_status(stored("OFF.AS"), "pending", enabled=False),
    ]

    configs = ticker_onboarding.tracked_ticker_configs(None, ready_only=False)

    assert [c.ticker for c in configs] == ["IWDA.AS", "NEW.AS", "BAD.AS"]


# --- normalize_symbol ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ASML.AS", "ASML.AS"),
        ("  asml.as ", "ASML.AS"),
        ("BTC-USD", "BTC-USD"),
        ("GC=F", "GC=F"),
        ("^GSPC", "^GSPC"),
        ("0700.HK", "0700.HK"),
        ("X" * 20, "X" * 20),
    ],
)
def test_normalize_symbol_accepts_provider_symbols(raw, expected):
    assert ticker_onboarding.normalize_symbol(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "X" * 21, "AS ML", "ASML;DROP", "ASML/AS", "../etc", "ÄSML", "AS\nML", "MY_TICKER"],
)
def test_normalize_symbol_refuses_anything_else(raw):
    assert ticker_onboarding.normalize_symbol(raw) is None


# --- lookup_symbol ---------------------------------------------------------------------------


class CountingSource:
    name = "yfinance"

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def lookup(self, ticker):
        self.calls.append(ticker)
        return LookupResult(
            outcome=self.outcome,
            ticker=ticker,
            display_name=None,
            currency=None,
            exchange=None,
            asset_type=None,
        )


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.mark.parametrize("outcome", [OUTCOME_FOUND, OUTCOME_NOT_FOUND, OUTCOME_UNSUPPORTED])
def test_a_definite_answer_is_cached(outcome):
    source = CountingSource(outcome)
    cache = ticker_onboarding.LookupCache(clock=FakeClock())

    ticker_onboarding.lookup_symbol("ASML.AS", source, cache)
    second = ticker_onboarding.lookup_symbol("ASML.AS", source, cache)

    assert source.calls == ["ASML.AS"]
    assert second.outcome == outcome


def test_unavailable_is_never_cached():
    # "Yahoo didn't answer" says nothing about the symbol, so the next try must ask again.
    source = CountingSource(OUTCOME_UNAVAILABLE)
    cache = ticker_onboarding.LookupCache(clock=FakeClock())

    ticker_onboarding.lookup_symbol("ASML.AS", source, cache)
    ticker_onboarding.lookup_symbol("ASML.AS", source, cache)

    assert source.calls == ["ASML.AS", "ASML.AS"]


def test_a_cached_answer_expires_after_the_ttl():
    source = CountingSource(OUTCOME_FOUND)
    clock = FakeClock()
    cache = ticker_onboarding.LookupCache(ttl_seconds=900, clock=clock)

    ticker_onboarding.lookup_symbol("ASML.AS", source, cache)
    clock.now = 899
    ticker_onboarding.lookup_symbol("ASML.AS", source, cache)
    clock.now = 901
    ticker_onboarding.lookup_symbol("ASML.AS", source, cache)

    assert source.calls == ["ASML.AS", "ASML.AS"]


# --- FX universe -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, expected",
    [
        ("EUR", "EUR"),
        ("USD", "USD"),
        ("GBp", "GBP"),
        ("ZAc", "ZAR"),
        ("ILA", "ILS"),
        (None, None),
        ("", None),
        ("usd", None),
        ("EURO", None),
    ],
)
def test_major_currency(code, expected):
    assert ticker_onboarding.major_currency(code) == expected


def with_currency(row, currency, *, enabled=True):
    from dataclasses import replace

    return replace(row, currency=currency, enabled=enabled)


def yaml_currencies(*codes, disabled=()):
    return CurrenciesConfig(
        base_currency="EUR",
        currencies=[CurrencyConfig(code=c, enabled=c not in disabled) for c in codes],
    )


def test_ticker_currencies_join_the_fx_universe(repo, caplog):
    caplog.set_level(logging.INFO)
    repo.rows = [
        with_currency(stored("VOD.L"), "GBp"),
        with_currency(stored("NPN.JO"), "ZAc"),
        with_currency(stored("ASML.AS"), "EUR"),
        with_currency(stored("SPY"), "USD"),
        with_currency(stored("CBA.AX"), "AUD", enabled=False),
        with_currency(stored("NEW.AS"), None),
    ]

    result = ticker_onboarding.currencies_with_tracked_tickers(None, yaml_currencies("EUR", "USD"))

    assert result.base_currency == "EUR"
    # Already-configured codes aren't repeated; a disabled ticker's currency isn't added.
    assert [(c.code, c.enabled) for c in result.currencies] == [
        ("EUR", True),
        ("USD", True),
        ("GBP", True),
        ("ZAR", True),
    ]
    assert "adding GBP, ZAR to the FX universe" in caplog.text


def test_a_currency_the_yaml_disables_stays_disabled_with_a_warning(repo, caplog):
    repo.rows = [with_currency(stored("VOD.L"), "GBp")]

    result = ticker_onboarding.currencies_with_tracked_tickers(
        None, yaml_currencies("EUR", "GBP", disabled=("GBP",))
    )

    assert [(c.code, c.enabled) for c in result.currencies] == [("EUR", True), ("GBP", False)]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "GBP" in warnings[0] and "VOD.L" in warnings[0]


def test_nothing_to_add_returns_the_yaml_config_unchanged(repo):
    config = yaml_currencies("EUR", "USD")
    repo.rows = [with_currency(stored("SPY"), "USD")]

    assert ticker_onboarding.currencies_with_tracked_tickers(None, config) is config
