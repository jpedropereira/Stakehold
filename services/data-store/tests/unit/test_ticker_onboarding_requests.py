"""The request side of ticker onboarding: looking a symbol up, adding it, and an admin changing
one. In-memory repository and lookup source; the SQL is covered against Postgres in
tests/integration/test_db_tracked_tickers.py."""

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from data_store import ticker_onboarding
from data_store.tracked_tickers.base import (
    OUTCOME_FOUND,
    OUTCOME_NOT_FOUND,
    OUTCOME_UNAVAILABLE,
    OUTCOME_UNSUPPORTED,
    LookupResult,
)
from data_store.tracked_tickers.repository import TrackedTicker

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
ALICE = UUID("00000000-0000-0000-0000-00000000000a")


def tracked(ticker, *, status="ready", origin="request", enabled=True, requested_by=None):
    return TrackedTicker(
        ticker=ticker,
        source="yfinance",
        display_name=f"{ticker} name",
        asset_type="equity",
        currency="EUR",
        exchange="Amsterdam",
        origin=origin,
        status=status,
        enabled=enabled,
        attempts=0,
        next_attempt_at=None,
        last_error=None,
        requested_by=requested_by,
        requested_at=NOW,
        ready_at=None,
        updated_at=NOW,
    )


class FakeRepository:
    def __init__(self):
        self.rows = {}
        self.requests_since = 0
        self.events = []

    def get_tracked_ticker(self, conn, ticker, source):
        return self.rows.get((ticker, source))

    def count_requests_since(self, conn, requested_by, since):
        self.events.append(("count", requested_by, since))
        return self.requests_since

    def insert_requested_ticker(self, conn, **fields):
        self.events.append(("insert", fields))
        key = (fields["ticker"], fields["source"])
        if key in self.rows:
            return False
        self.rows[key] = replace(
            tracked(fields["ticker"], status="pending"),
            display_name=fields["display_name"],
            asset_type=fields["asset_type"],
            currency=fields["currency"],
            exchange=fields["exchange"],
            requested_by=fields["requested_by"],
        )
        return True

    def list_tracked_tickers(self, conn, **filters):
        self.events.append(("list", filters))
        return list(self.rows.values())

    def set_enabled(self, conn, ticker, source, enabled):
        self.events.append(("set_enabled", ticker, enabled))
        self.rows[(ticker, source)] = replace(self.rows[(ticker, source)], enabled=enabled)

    def reset_for_retry(self, conn, ticker, source):
        self.events.append(("retry", ticker))
        self.rows[(ticker, source)] = replace(self.rows[(ticker, source)], status="pending")

    def of(self, kind):
        return [e for e in self.events if e[0] == kind]


class Source:
    name = "yfinance"

    def __init__(self, outcome=OUTCOME_FOUND, canonical=None):
        self.outcome = outcome
        self.canonical = canonical
        self.calls = []

    def lookup(self, ticker):
        self.calls.append(ticker)
        return LookupResult(
            outcome=self.outcome,
            ticker=self.canonical or ticker,
            display_name="ASML Holding N.V.",
            currency="EUR",
            exchange="Amsterdam",
            asset_type="equity" if self.outcome == OUTCOME_FOUND else None,
        )


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepository()
    for name in (
        "get_tracked_ticker",
        "count_requests_since",
        "insert_requested_ticker",
        "list_tracked_tickers",
        "set_enabled",
        "reset_for_retry",
    ):
        monkeypatch.setattr(ticker_onboarding.tracked_tickers_repository, name, getattr(fake, name))
    return fake


def cache():
    return ticker_onboarding.LookupCache()


# --- lookup_ticker -------------------------------------------------------------------------------


def test_lookup_refuses_a_symbol_that_cannot_be_one(repo):
    with pytest.raises(ticker_onboarding.InvalidSymbol):
        ticker_onboarding.lookup_ticker(None, "AS ML", Source(), cache())


def test_lookup_of_a_tracked_ticker_reports_its_status_without_asking_the_provider(repo):
    repo.rows[("ASML.AS", "yfinance")] = tracked("ASML.AS", status="backfilling")
    source = Source()

    result = ticker_onboarding.lookup_ticker(None, "asml.as", source, cache())

    assert source.calls == []
    assert result["outcome"] == "tracked"
    assert result["status"] == "backfilling"
    assert result["ticker"] == "ASML.AS"
    assert result["display_name"] == "ASML.AS name"


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (OUTCOME_FOUND, "available"),
        (OUTCOME_UNSUPPORTED, "unsupported"),
        (OUTCOME_NOT_FOUND, "not_found"),
        (OUTCOME_UNAVAILABLE, "unavailable"),
    ],
)
def test_lookup_of_an_untracked_ticker_reports_the_provider_outcome(repo, outcome, expected):
    result = ticker_onboarding.lookup_ticker(None, "ASML.AS", Source(outcome), cache())

    assert result["outcome"] == expected
    assert result["status"] is None


def test_lookup_finds_a_tracked_ticker_under_the_provider_spelling(repo):
    repo.rows[("BRK-B", "yfinance")] = tracked("BRK-B")

    result = ticker_onboarding.lookup_ticker(None, "BRK.B", Source(canonical="BRK-B"), cache())

    assert (result["outcome"], result["ticker"]) == ("tracked", "BRK-B")


def test_an_available_lookup_describes_the_listing(repo):
    result = ticker_onboarding.lookup_ticker(None, "ASML.AS", Source(), cache())

    assert result == {
        "ticker": "ASML.AS",
        "outcome": "available",
        "status": None,
        "display_name": "ASML Holding N.V.",
        "currency": "EUR",
        "exchange": "Amsterdam",
        "asset_type": "equity",
    }


# --- add_tracked_ticker --------------------------------------------------------------------------


def add(repo, raw="ASML.AS", *, source=None, enforce_limit=True, limit=10):
    return ticker_onboarding.add_tracked_ticker(
        None,
        raw,
        requested_by=ALICE,
        enforce_limit=enforce_limit,
        limit_per_day=limit,
        lookup_source=source or Source(),
        cache=cache(),
        now=NOW,
    )


def test_adding_a_found_ticker_inserts_a_pending_request_row(repo, caplog):
    caplog.set_level(logging.INFO)

    row, created = add(repo, "asml.as")

    assert created is True
    assert (row.ticker, row.status, row.requested_by) == ("ASML.AS", "pending", ALICE)
    [(_, fields)] = repo.of("insert")
    assert fields == {
        "ticker": "ASML.AS",
        "source": "yfinance",
        "display_name": "ASML Holding N.V.",
        "asset_type": "equity",
        "currency": "EUR",
        "exchange": "Amsterdam",
        "requested_by": ALICE,
    }
    assert "ASML.AS requested" in caplog.text


def test_adding_a_ticker_already_tracked_returns_it_without_counting_or_asking(repo):
    repo.rows[("ASML.AS", "yfinance")] = tracked("ASML.AS")
    repo.requests_since = 99
    source = Source()

    row, created = add(repo, source=source)

    assert created is False
    assert row.ticker == "ASML.AS"
    assert source.calls == []
    assert repo.of("insert") == []


def test_the_daily_limit_is_checked_before_the_provider_is_asked(repo):
    repo.requests_since = 10
    source = Source()

    with pytest.raises(ticker_onboarding.RequestLimitReached) as exc:
        add(repo, source=source, limit=10)

    assert exc.value.limit == 10
    assert source.calls == []
    assert repo.of("count") == [("count", ALICE, NOW - timedelta(days=1))]


def test_an_admin_has_no_limit(repo):
    repo.requests_since = 500

    row, created = add(repo, enforce_limit=False)

    assert created is True
    assert repo.of("count") == []


@pytest.mark.parametrize(
    "outcome, error",
    [
        (OUTCOME_NOT_FOUND, ticker_onboarding.NotOnProvider),
        (OUTCOME_UNSUPPORTED, ticker_onboarding.CannotBeHeld),
        (OUTCOME_UNAVAILABLE, ticker_onboarding.ProviderUnavailable),
    ],
)
def test_a_ticker_that_cannot_be_added_raises_and_inserts_nothing(repo, outcome, error):
    with pytest.raises(error):
        add(repo, source=Source(outcome))

    assert repo.of("insert") == []


def test_adding_under_another_spelling_of_a_tracked_ticker_returns_that_row(repo):
    repo.rows[("BRK-B", "yfinance")] = tracked("BRK-B")

    row, created = add(repo, "BRK.B", source=Source(canonical="BRK-B"))

    assert (row.ticker, created) == ("BRK-B", False)
    assert repo.of("insert") == []


def test_adding_refuses_an_invalid_symbol(repo):
    with pytest.raises(ticker_onboarding.InvalidSymbol):
        add(repo, "ASML;DROP")


# --- list / update -------------------------------------------------------------------------------


def test_list_passes_its_filters_through(repo):
    ticker_onboarding.list_tracked(
        None, source="yfinance", requested_by=ALICE, statuses=["pending"], tickers=["X"]
    )

    assert repo.of("list") == [
        (
            "list",
            {
                "source": "yfinance",
                "requested_by": ALICE,
                "statuses": ["pending"],
                "tickers": ["X"],
            },
        )
    ]


def test_update_of_an_unknown_ticker_raises(repo):
    with pytest.raises(ticker_onboarding.TrackedTickerNotFound):
        ticker_onboarding.update_tracked(None, "NOPE", "yfinance", enabled=False, retry=False)


def test_disabling_a_requested_ticker(repo):
    repo.rows[("ASML.AS", "yfinance")] = tracked("ASML.AS")

    row = ticker_onboarding.update_tracked(None, "ASML.AS", "yfinance", enabled=False, retry=False)

    assert row.enabled is False
    assert repo.of("set_enabled") == [("set_enabled", "ASML.AS", False)]


def test_a_config_ticker_cannot_be_enabled_or_disabled_through_the_api(repo):
    # The next YAML sync would undo it, so the change belongs in tracked_tickers.yaml.
    repo.rows[("IWDA.AS", "yfinance")] = tracked("IWDA.AS", origin="config")

    with pytest.raises(ticker_onboarding.ManagedByConfig):
        ticker_onboarding.update_tracked(None, "IWDA.AS", "yfinance", enabled=False, retry=False)

    assert repo.of("set_enabled") == []


def test_setting_enabled_to_its_current_value_is_not_a_change(repo):
    repo.rows[("IWDA.AS", "yfinance")] = tracked("IWDA.AS", origin="config")

    row = ticker_onboarding.update_tracked(None, "IWDA.AS", "yfinance", enabled=True, retry=False)

    assert row.enabled is True
    assert repo.of("set_enabled") == []


def test_retrying_a_failed_ticker_puts_it_back_in_the_queue(repo):
    repo.rows[("GHOST", "yfinance")] = tracked("GHOST", status="failed")

    row = ticker_onboarding.update_tracked(None, "GHOST", "yfinance", enabled=None, retry=True)

    assert row.status == "pending"
    assert repo.of("retry") == [("retry", "GHOST")]


@pytest.mark.parametrize("status", ["pending", "backfilling", "ready"])
def test_only_a_failed_ticker_can_be_retried(repo, status):
    repo.rows[("SPY", "yfinance")] = tracked("SPY", status=status)

    with pytest.raises(ticker_onboarding.NotRetryable):
        ticker_onboarding.update_tracked(None, "SPY", "yfinance", enabled=None, retry=True)


def test_lookup_source_for_an_unknown_source_raises():
    with pytest.raises(ticker_onboarding.UnknownLookupSource):
        ticker_onboarding.lookup_source_for("bloomberg")


def test_lookup_source_for_yfinance():
    assert ticker_onboarding.lookup_source_for("yfinance").name == "yfinance"
