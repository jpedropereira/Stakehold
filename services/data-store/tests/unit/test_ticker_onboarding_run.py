"""process_ticker_requests: one onboarding run over the tracked_tickers table.

The repository is an in-memory stand-in and the pipeline flows are stubs, so these tests pin down
the state machine -- which ticker is picked up, what counts as success, how a failure is retried
or given up on -- rather than any SQL or provider call. The SQL is covered against Postgres in
tests/integration/test_db_tracked_tickers.py.
"""

import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from data_store import ticker_onboarding
from data_store.config_loader import CurrenciesConfig, CurrencyConfig
from data_store.tracked_tickers.base import (
    OUTCOME_FOUND,
    OUTCOME_UNAVAILABLE,
    LookupResult,
)
from data_store.tracked_tickers.repository import TrackedTicker

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 10, 10)


def row(ticker, *, currency="USD", attempts=0, status="pending", origin="request"):
    return TrackedTicker(
        ticker=ticker,
        source="yfinance",
        display_name=ticker,
        asset_type="equity",
        currency=currency,
        exchange=None,
        origin=origin,
        status=status,
        enabled=True,
        attempts=attempts,
        next_attempt_at=None,
        last_error=None,
        requested_by=None,
        requested_at=NOW,
        ready_at=None,
        updated_at=NOW,
    )


def currencies(*codes, disabled=()):
    return CurrenciesConfig(
        base_currency="EUR",
        currencies=[CurrencyConfig(code=c, enabled=c not in disabled) for c in codes],
    )


class FakeRepository:
    def __init__(self):
        self.rows = []
        self.due = []
        self.stuck = []
        self.unclaimable = set()
        self.events = []

    def get_tracked_tickers(self, conn):
        return list(self.rows)

    def reset_stuck_backfilling(self, conn, *, stale_before):
        self.events.append(("reset", stale_before))
        return list(self.stuck)

    def get_due_tickers(self, conn, *, now, limit):
        self.events.append(("due", now, limit))
        return list(self.due)

    def claim_for_backfill(self, conn, ticker, source):
        self.events.append(("claim", ticker))
        return ticker not in self.unclaimable

    def set_listing_details(self, conn, ticker, source, *, currency, exchange):
        self.events.append(("details", ticker, currency, exchange))
        # Persisted, like the real one, so the FX universe read back afterwards includes it.
        self.rows = [
            replace(r, currency=currency, exchange=exchange) if r.ticker == ticker else r
            for r in self.rows
        ]

    def mark_ready(self, conn, ticker, source):
        self.events.append(("ready", ticker))

    def record_failed_attempt(
        self, conn, ticker, source, *, attempts, status, next_attempt_at, error
    ):
        self.events.append(("failed_attempt", ticker, attempts, status, next_attempt_at, error))

    def of(self, kind):
        return [e for e in self.events if e[0] == kind]


class Pipeline:
    """Stubs for the flows onboarding calls, with knobs for each failure it must handle."""

    def __init__(self):
        self.calls = []
        self.backfill_status = "ok"
        self.backfill_raises = None
        self.stored_tickers = None  # None: every backfilled ticker ends up with prices
        self.fx_status = "ok"
        self.fx_stored = True
        self.returns_status = "ok"

    def backfill_market_data(self, conn, funds, tracked, suffixes, earliest, end, delay, **kw):
        self.calls.append(("backfill", [t.ticker for t in tracked], funds, earliest, end, kw))
        if self.backfill_raises:
            raise self.backfill_raises
        return {"status": self.backfill_status, "rows_saved": 100}

    def backfill_fx_rates(self, conn, config, earliest, end, delay, **kw):
        self.calls.append(("fx", kw["only_currencies"], [c.code for c in config.currencies]))
        return {"status": self.fx_status, "rows_saved": 10}

    def calculate_returns(self, conn, only_tickers=None):
        self.calls.append(("returns", only_tickers))
        return {"status": self.returns_status, "rows_saved": 99}

    def ticker_exists(self, conn, ticker, source):
        return self.stored_tickers is None or ticker in self.stored_tickers

    def get_earliest_fx_dates(self, conn, source, pairs):
        return {pair: date(2000, 1, 1) for pair in pairs} if self.fx_stored else {}

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]


class Lookup:
    name = "yfinance"

    def __init__(self, outcome=OUTCOME_FOUND, currency="ZAc", exchange="Johannesburg"):
        self.outcome = outcome
        self.currency = currency
        self.exchange = exchange
        self.calls = []

    def lookup(self, ticker):
        self.calls.append(ticker)
        return LookupResult(self.outcome, ticker, ticker, self.currency, self.exchange, "equity")


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepository()
    for name in (
        "get_tracked_tickers",
        "reset_stuck_backfilling",
        "get_due_tickers",
        "claim_for_backfill",
        "set_listing_details",
        "mark_ready",
        "record_failed_attempt",
    ):
        monkeypatch.setattr(ticker_onboarding.tracked_tickers_repository, name, getattr(fake, name))
    return fake


@pytest.fixture
def pipe(monkeypatch):
    fake = Pipeline()
    for name in ("backfill_market_data", "backfill_fx_rates", "calculate_returns"):
        monkeypatch.setattr(ticker_onboarding.pipeline, name, getattr(fake, name))
    monkeypatch.setattr(ticker_onboarding.returns_repository, "ticker_exists", fake.ticker_exists)
    monkeypatch.setattr(
        ticker_onboarding.fx_repository, "get_earliest_fx_dates", fake.get_earliest_fx_dates
    )
    return fake


def run(lookup=None, config=None):
    return ticker_onboarding.process_ticker_requests(
        None,
        currencies=config or currencies("EUR", "USD"),
        lookup_source=lookup or Lookup(),
        now=NOW,
        today=TODAY,
    )


def test_nothing_due_is_nothing_to_do(repo, pipe, caplog):
    caplog.set_level(logging.INFO)

    result = run()

    assert result["status"] == "nothing_to_do"
    assert pipe.calls == []
    assert "0 ticker(s) due" in caplog.text
    assert "nothing to do" in caplog.text


def test_asks_for_due_tickers_with_a_cap(repo, pipe):
    run()

    assert repo.of("due") == [("due", NOW, ticker_onboarding.MAX_TICKERS_PER_RUN)]


def test_a_due_ticker_is_backfilled_converted_calculated_and_marked_ready(repo, pipe, caplog):
    caplog.set_level(logging.INFO)
    repo.due = [row("SPY", currency="USD")]

    result = run()

    # Full history: no earliest date (backfill's "as far back as the source goes"), no funds.
    assert pipe.of("backfill") == [
        ("backfill", ["SPY"], [], None, TODAY, {"only_tickers": {"SPY"}})
    ]
    assert pipe.of("fx")[0][1] == {"USD"}
    assert pipe.of("returns") == [("returns", {"SPY"})]
    assert repo.of("claim") == [("claim", "SPY")]
    assert repo.of("ready") == [("ready", "SPY")]
    assert result["status"] == "ok"
    assert result["tickers_ready"] == 1
    assert result["rows_saved"] == 110
    assert "SPY is ready" in caplog.text


def test_a_ticker_in_the_base_currency_needs_no_fx(repo, pipe):
    repo.due = [row("ASML.AS", currency="EUR")]

    run()

    assert pipe.of("fx") == []
    assert repo.of("ready") == [("ready", "ASML.AS")]


def test_a_minor_unit_currency_backfills_its_major_currency(repo, pipe):
    repo.due = [row("VOD.L", currency="GBp")]
    repo.rows = repo.due

    run()

    assert pipe.of("fx")[0][1] == {"GBP"}
    # The FX flow is handed the widened universe, or it would find GBP untracked.
    assert "GBP" in pipe.of("fx")[0][2]


def test_a_config_row_without_a_currency_gets_one_from_the_lookup(repo, pipe):
    repo.due = [row("NPN.JO", currency=None, origin="config")]
    repo.rows = list(repo.due)
    lookup = Lookup(currency="ZAc", exchange="Johannesburg")

    run(lookup=lookup)

    assert lookup.calls == ["NPN.JO"]
    assert repo.of("details") == [("details", "NPN.JO", "ZAc", "Johannesburg")]
    assert pipe.of("fx")[0][1] == {"ZAR"}
    assert repo.of("ready") == [("ready", "NPN.JO")]


def test_an_unanswered_lookup_does_not_stop_onboarding(repo, pipe, caplog):
    repo.due = [row("NPN.JO", currency=None, origin="config")]

    run(lookup=Lookup(outcome=OUTCOME_UNAVAILABLE))

    assert repo.of("details") == []
    assert pipe.of("fx") == []
    assert repo.of("ready") == [("ready", "NPN.JO")]
    assert "currency of NPN.JO" in caplog.text


def test_a_currency_the_yaml_disables_is_not_backfilled(repo, pipe):
    repo.due = [row("SPY", currency="USD")]

    run(config=currencies("EUR", "USD", disabled=("USD",)))

    assert pipe.of("fx") == []
    assert repo.of("ready") == [("ready", "SPY")]


def test_no_stored_prices_after_backfill_is_a_failed_attempt_retried_later(repo, pipe, caplog):
    repo.due = [row("GHOST", attempts=0)]
    pipe.stored_tickers = set()

    result = run()

    [(_, ticker, attempts, status, next_attempt_at, error)] = repo.of("failed_attempt")
    assert (ticker, attempts, status) == ("GHOST", 1, "pending")
    assert next_attempt_at == NOW + timedelta(minutes=10)
    assert "no price history" in error
    assert repo.of("ready") == []
    # A retry still to come is not a failed run: the DAG only goes red when a ticker gives up.
    assert result["status"] == "ok"
    assert result["tickers_retrying"] == 1
    assert "GHOST" in caplog.text


def test_the_retry_delay_doubles(repo, pipe):
    repo.due = [row("GHOST", attempts=1)]
    pipe.stored_tickers = set()

    run()

    [(_, _, attempts, status, next_attempt_at, _)] = repo.of("failed_attempt")
    assert (attempts, status) == (2, "pending")
    assert next_attempt_at == NOW + timedelta(minutes=20)


def test_the_third_failure_gives_up_and_fails_the_run(repo, pipe):
    repo.due = [row("GHOST", attempts=2)]
    pipe.stored_tickers = set()

    result = run()

    [(_, _, attempts, status, next_attempt_at, _)] = repo.of("failed_attempt")
    assert (attempts, status, next_attempt_at) == (3, "failed", None)
    assert result["status"] == "partial"
    assert result["tickers_failed"] == 1


def test_a_partial_backfill_is_a_failed_attempt(repo, pipe):
    repo.due = [row("SPY")]
    pipe.backfill_status = "partial"

    run()

    assert "price history couldn't be saved" in repo.of("failed_attempt")[0][5]
    assert pipe.of("returns") == []


def test_fx_that_never_lands_is_a_failed_attempt(repo, pipe):
    repo.due = [row("SPY", currency="USD")]
    pipe.fx_stored = False

    run()

    assert "USD" in repo.of("failed_attempt")[0][5]
    assert repo.of("ready") == []


def test_a_partial_returns_calculation_is_a_failed_attempt(repo, pipe):
    repo.due = [row("SPY")]
    pipe.returns_status = "partial"

    run()

    assert "returns couldn't be calculated" in repo.of("failed_attempt")[0][5]


def test_an_unexpected_error_fails_only_that_ticker(repo, pipe, caplog):
    repo.due = [row("BOOM"), row("SPY")]
    calls = {"n": 0}
    original = pipe.backfill_market_data

    def first_explodes(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("kaboom")
        return original(*args, **kwargs)

    ticker_onboarding.pipeline.backfill_market_data = first_explodes

    result = run()

    assert [e[1] for e in repo.of("failed_attempt")] == ["BOOM"]
    assert "kaboom" in repo.of("failed_attempt")[0][5]
    assert repo.of("ready") == [("ready", "SPY")]
    assert result["tickers_ready"] == 1
    assert "kaboom" in caplog.text


def test_a_ticker_another_run_already_claimed_is_left_to_it(repo, pipe):
    repo.due = [row("SPY")]
    repo.unclaimable = {"SPY"}

    result = run()

    assert pipe.calls == []
    assert result["tickers_ready"] == 0


def test_stuck_backfills_are_reset_first_and_reported(repo, pipe, caplog):
    repo.stuck = ["STUCK.AS"]

    result = run()

    assert repo.events[0] == ("reset", NOW - ticker_onboarding.STUCK_AFTER)
    assert result["tickers_reset"] == 1
    assert "STUCK.AS" in caplog.text


def test_summary_line_counts_every_outcome(repo, pipe, caplog):
    caplog.set_level(logging.INFO)
    repo.due = [row("SPY"), replace(row("GHOST"), attempts=2)]
    pipe.stored_tickers = {"SPY"}

    run()

    assert "1 ready, 0 retrying, 1 failed, 0 reset" in caplog.text
