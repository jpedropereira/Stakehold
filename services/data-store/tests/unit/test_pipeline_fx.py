"""Tests for the FX-rate flows in pipeline.py -- ingest_fx_rates and backfill_fx_rates.

Much simpler than the market-data flows: one fixed source, no exchange resolution, no fund
coupling, no split-restatement pass. See test_pipeline.py for the market-data equivalents these
were modeled on.
"""

import logging
import pickle
from datetime import date

import pandas as pd
import pytest
from data_store import pipeline
from data_store.config_loader import CurrenciesConfig, CurrencyConfig


def make_currencies(*, base="EUR", codes=("EUR", "USD", "GBP"), disabled=()):
    return CurrenciesConfig(
        base_currency=base,
        currencies=[CurrencyConfig(code=c, enabled=c not in disabled) for c in codes],
    )


class FakeFxRateSource:
    name = "yfinance"

    def __init__(self):
        self.fetch_calls = []

    def fetch(self, pairs, start_date, end_date):
        self.fetch_calls.append((sorted(pairs), start_date, end_date))
        return pd.DataFrame(
            {
                "base_currency": [b for b, _ in pairs],
                "quote_currency": [q for _, q in pairs],
                "rate_date": [start_date] * len(pairs),
                "rate": [1.1] * len(pairs),
            }
        )


def _patch_fx_source(monkeypatch, fake_source, save_calls):
    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: fake_source)
    monkeypatch.setattr(
        pipeline,
        "save_fx_rates",
        lambda conn_arg, source, df: save_calls.append(
            (source, sorted(zip(df["base_currency"], df["quote_currency"])))
        )
        or len(df),
    )


# --- ingest_fx_rates ----------------------------------------------------------------------------


def test_ingest_fx_rates_prices_every_enabled_currency_against_the_base(monkeypatch):
    conn = object()
    fake_source = FakeFxRateSource()
    save_calls = []
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP", "PLN"))

    _patch_fx_source(monkeypatch, fake_source, save_calls)

    result = pipeline.ingest_fx_rates(conn, currencies, date(2026, 1, 1), date(2026, 1, 10))

    assert result["rows_saved"] == 3
    assert fake_source.fetch_calls[0][0] == [("EUR", "GBP"), ("EUR", "PLN"), ("EUR", "USD")]
    assert save_calls == [("yfinance", [("EUR", "GBP"), ("EUR", "PLN"), ("EUR", "USD")])]


def test_ingest_fx_rates_excludes_the_base_currency_itself(monkeypatch):
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    _patch_fx_source(monkeypatch, fake_source, [])

    pipeline.ingest_fx_rates(conn, currencies, date(2026, 1, 1), date(2026, 1, 10))

    assert ("EUR", "EUR") not in fake_source.fetch_calls[0][0]


def test_ingest_fx_rates_excludes_disabled_currencies(monkeypatch):
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP"), disabled=("GBP",))

    _patch_fx_source(monkeypatch, fake_source, [])

    pipeline.ingest_fx_rates(conn, currencies, date(2026, 1, 1), date(2026, 1, 10))

    assert fake_source.fetch_calls[0][0] == [("EUR", "USD")]


def test_ingest_fx_rates_filters_to_only_currencies_when_given(monkeypatch):
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP", "PLN"))

    _patch_fx_source(monkeypatch, fake_source, [])

    result = pipeline.ingest_fx_rates(
        conn, currencies, date(2026, 1, 1), date(2026, 1, 10), only_currencies={"USD"}
    )

    assert fake_source.fetch_calls[0][0] == [("EUR", "USD")]
    assert result["rows_saved"] == 1


def test_ingest_fx_rates_warns_when_a_requested_currency_matches_nothing(monkeypatch, caplog):
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    _patch_fx_source(monkeypatch, fake_source, [])

    with caplog.at_level(logging.WARNING):
        pipeline.ingest_fx_rates(
            conn,
            currencies,
            date(2026, 1, 1),
            date(2026, 1, 10),
            only_currencies={"USD", "TYPO"},
        )

    assert "TYPO" in caplog.text
    assert "USD" not in caplog.text


def test_requesting_a_currency_that_matches_nothing_is_a_failure_not_a_quiet_success(monkeypatch):
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    _patch_fx_source(monkeypatch, fake_source, [])

    result = pipeline.ingest_fx_rates(
        conn, currencies, date(2026, 1, 1), date(2026, 1, 10), only_currencies={"NOPE"}
    )

    assert result["status"] == "partial"
    assert result["currencies_unmatched"] == 1


def test_ingest_fx_rates_reports_nothing_to_do_when_only_base_is_enabled(monkeypatch):
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"), disabled=("USD",))

    _patch_fx_source(monkeypatch, fake_source, [])

    result = pipeline.ingest_fx_rates(conn, currencies, date(2026, 1, 1), date(2026, 1, 10))

    assert result["status"] == "nothing_to_do"
    assert result["rows_saved"] == 0
    assert fake_source.fetch_calls == []


def test_ingest_fx_rates_returns_zero_when_source_has_no_data(monkeypatch):
    conn = object()

    class EmptySource:
        name = "yfinance"

        def fetch(self, pairs, start_date, end_date):
            return pd.DataFrame()

    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))
    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: EmptySource())
    save_calls = []
    monkeypatch.setattr(pipeline, "save_fx_rates", lambda *a: save_calls.append(a) or len(a[-1]))

    result = pipeline.ingest_fx_rates(conn, currencies, date(2026, 1, 1), date(2026, 1, 10))

    assert result["rows_saved"] == 0
    assert save_calls == []


def test_ingest_fx_rates_logs_run_summary(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    _patch_fx_source(monkeypatch, fake_source, [])

    pipeline.ingest_fx_rates(conn, currencies, date(2026, 1, 1), date(2026, 1, 10))

    assert "1" in caplog.text


def test_ingest_result_reports_counts_and_ok_status(monkeypatch):
    conn = object()
    fake_source = FakeFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP"))

    _patch_fx_source(monkeypatch, fake_source, [])

    result = pipeline.ingest_fx_rates(conn, currencies, date(2026, 1, 1), date(2026, 1, 10))

    assert result["status"] == "ok"
    assert result["currencies_resolved"] == 2
    assert result["rows_saved"] == 2


# --- backfill_fx_rates ---------------------------------------------------------------------------


class FakeSingleFxRateSource:
    name = "yfinance"

    def __init__(self, *, fail_pairs=None):
        self.fetch_calls = []
        self.fail_pairs = fail_pairs or set()

    def fetch(self, pairs, start_date, end_date):
        pair = pairs[0]
        self.fetch_calls.append((pair, start_date, end_date))
        if pair in self.fail_pairs:
            raise RuntimeError(f"{pair} failed")
        return pd.DataFrame(
            {
                "base_currency": [pair[0]],
                "quote_currency": [pair[1]],
                "rate_date": [start_date],
                "rate": [1.1],
            }
        )


def _run_backfill(monkeypatch, source, currencies, *, floors, **kwargs):
    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr(pipeline, "get_earliest_fx_dates", lambda c, s, p: floors)
    monkeypatch.setattr(pipeline, "save_fx_rates", lambda c, s, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)
    return pipeline.backfill_fx_rates(
        object(),
        currencies,
        kwargs.pop("earliest_date", None),
        date(2026, 1, 1),
        delay_seconds=0,
        **kwargs,
    )


def test_backfill_fetches_full_range_when_no_existing_data(monkeypatch):
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    _run_backfill(monkeypatch, source, currencies, floors={})

    assert source.fetch_calls == [
        (("EUR", "USD"), pipeline.UNBOUNDED_EARLIEST_DATE, date(2026, 1, 1))
    ]


def test_backfill_skips_pair_already_covered(monkeypatch):
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    result = _run_backfill(
        monkeypatch, source, currencies, floors={("EUR", "USD"): date(1990, 1, 1)}
    )

    assert source.fetch_calls == []
    assert result["rows_saved"] == 0
    assert result["currencies_skipped"] == 1


def test_backfill_with_explicit_earliest_date_still_fills_the_gap(monkeypatch):
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    _run_backfill(
        monkeypatch,
        source,
        currencies,
        floors={("EUR", "USD"): date(2010, 1, 1)},
        earliest_date=date(1990, 1, 1),
    )

    assert source.fetch_calls == [(("EUR", "USD"), date(1990, 1, 1), date(2010, 1, 1))]


def test_backfill_force_refresh_rewrites_a_pair_that_already_has_data(monkeypatch):
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    result = _run_backfill(
        monkeypatch,
        source,
        currencies,
        floors={("EUR", "USD"): date(1990, 1, 1)},
        force_refresh=True,
    )

    assert source.fetch_calls == [
        (("EUR", "USD"), pipeline.UNBOUNDED_EARLIEST_DATE, date(2026, 1, 1))
    ]
    assert result["currencies_skipped"] == 0


def test_backfill_continues_after_one_pair_fails(monkeypatch):
    source = FakeSingleFxRateSource(fail_pairs={("EUR", "USD")})
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP"))

    result = _run_backfill(monkeypatch, source, currencies, floors={})

    assert result["rows_saved"] == 1  # only GBP saved
    assert result["currencies_failed"] == 1
    assert result["status"] == "partial"


def test_backfill_continues_after_one_pair_fails_to_save(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP"))

    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr(pipeline, "get_earliest_fx_dates", lambda c, s, p: {})

    def fake_save(conn, source_name, df):
        if df["quote_currency"].iloc[0] == "USD":
            raise RuntimeError("numeric field overflow")
        return len(df)

    monkeypatch.setattr(pipeline, "save_fx_rates", fake_save)
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    result = pipeline.backfill_fx_rates(
        object(), currencies, None, date(2026, 1, 1), delay_seconds=0
    )

    assert result["rows_saved"] == 1  # only GBP saved
    assert result["currencies_failed"] == 1


def test_backfill_sleeps_after_fetched_and_failed_pairs_not_after_skips(monkeypatch):
    source = FakeSingleFxRateSource(fail_pairs={("EUR", "GBP")})
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP", "PLN"))
    sleeps = []

    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: source)
    monkeypatch.setattr(
        pipeline,
        "get_earliest_fx_dates",
        lambda c, s, p: {("EUR", "PLN"): date(1990, 1, 1)},
    )
    monkeypatch.setattr(pipeline, "save_fx_rates", lambda c, s, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", sleeps.append)

    pipeline.backfill_fx_rates(object(), currencies, None, date(2026, 1, 1), delay_seconds=5)

    # USD (fetched) and GBP (failed) both sleep; PLN (skipped, already covered) does not.
    assert sleeps == [5, 5]


def test_backfill_filters_to_only_currencies_when_given(monkeypatch):
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD", "GBP"))

    result = _run_backfill(monkeypatch, source, currencies, floors={}, only_currencies={"USD"})

    assert source.fetch_calls == [
        (("EUR", "USD"), pipeline.UNBOUNDED_EARLIEST_DATE, date(2026, 1, 1))
    ]
    assert result["currencies_resolved"] == 1


def test_backfill_warns_when_a_requested_currency_matches_nothing(monkeypatch, caplog):
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    with caplog.at_level(logging.WARNING):
        _run_backfill(monkeypatch, source, currencies, floors={}, only_currencies={"NOPE"})

    assert "NOPE" in caplog.text


def test_backfill_result_reports_nothing_to_do(monkeypatch):
    source = FakeSingleFxRateSource()
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"), disabled=("USD",))

    result = _run_backfill(monkeypatch, source, currencies, floors={})

    assert result["status"] == "nothing_to_do"
    assert result["currencies_resolved"] == 0
    assert result["rows_saved"] == 0


def test_backfill_result_separates_skipped_fetched_and_failed(monkeypatch):
    source = FakeSingleFxRateSource(fail_pairs={("EUR", "BAD")})
    currencies = make_currencies(base="EUR", codes=("EUR", "NEW", "DONE", "BAD"))

    result = _run_backfill(
        monkeypatch, source, currencies, floors={("EUR", "DONE"): date(2020, 1, 1)}
    )

    assert result["status"] == "partial"
    assert result["currencies_resolved"] == 3
    assert result["currencies_fetched"] == 1
    assert result["currencies_skipped"] == 1
    assert result["currencies_failed"] == 1
    assert result["rows_saved"] == 1


def test_backfill_silences_yfinances_own_noisy_no_data_logging(monkeypatch):
    yf_logger = logging.getLogger("yfinance")
    original_level = yf_logger.level
    yf_logger.setLevel(logging.NOTSET)

    levels_during_fetch = []

    class LevelObservingSource(FakeSingleFxRateSource):
        def fetch(self, pairs, start_date, end_date):
            levels_during_fetch.append(logging.getLogger("yfinance").level)
            return super().fetch(pairs, start_date, end_date)

    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))

    monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", LevelObservingSource)
    monkeypatch.setattr(pipeline, "get_earliest_fx_dates", lambda c, s, p: {})
    monkeypatch.setattr(pipeline, "save_fx_rates", lambda c, s, df: len(df))
    monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)

    try:
        pipeline.backfill_fx_rates(
            object(), currencies, date(1900, 1, 1), date(2026, 1, 1), delay_seconds=0
        )

        assert levels_during_fetch == [logging.CRITICAL]
    finally:
        yf_logger.setLevel(original_level)


@pytest.mark.parametrize("flow", ["ingest_fx_rates", "backfill_fx_rates"])
def test_every_flow_result_survives_a_process_boundary(monkeypatch, flow):
    # Same contract as the market-data flows -- see test_pipeline.py's equivalent for why.
    currencies = make_currencies(base="EUR", codes=("EUR", "USD"))
    if flow == "ingest_fx_rates":
        _patch_fx_source(monkeypatch, FakeFxRateSource(), [])
        result = pipeline.ingest_fx_rates(object(), currencies, date(2026, 1, 1), date(2026, 1, 10))
    else:
        monkeypatch.setitem(pipeline.FX_RATE_SOURCES, "yfinance", lambda: FakeSingleFxRateSource())
        monkeypatch.setattr(pipeline, "get_earliest_fx_dates", lambda c, s, p: {})
        monkeypatch.setattr(pipeline, "save_fx_rates", lambda c, s, df: len(df))
        monkeypatch.setattr("data_store.pipeline.time.sleep", lambda s: None)
        result = pipeline.backfill_fx_rates(
            object(), currencies, None, date(2026, 1, 1), delay_seconds=0
        )

    assert isinstance(result, dict)
    for key, value in result.items():
        assert type(key) is str, f"{key!r} is not a plain str"
        assert type(value) in (int, str), f"{key}={value!r} is {type(value).__name__}"
    assert pickle.loads(pickle.dumps(result)) == result
    assert {"status", "rows_saved"} <= result.keys()
