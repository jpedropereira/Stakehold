"""Unit tests for pipeline.calculate_returns.

Split out from test_pipeline.py (already at the 64KB file-size guideline) rather than grown
inside it -- see .claude/rules/context-management.md.
"""

import logging

import pandas as pd
import pytest
from data_store import pipeline


def make_batch(tickers):
    return pd.DataFrame(
        {
            "ticker": tickers,
            "price_date": ["2026-01-01"] * len(tickers),
            "close": [100.0] * len(tickers),
            "dividends": [0.0] * len(tickers),
            "stock_splits": [0.0] * len(tickers),
        }
    )


def identity_compute_returns(batch):
    # Stands in for the real calculator: this module tests orchestration (batching, failure
    # isolation, ticker filtering, summaries), not the arithmetic -- that's
    # tests/unit/returns/test_calculator.py's job.
    out = batch[["ticker", "price_date"]].copy()
    out["split_adjusted_close"] = batch["close"]
    out["future_split_factor"] = 1.0
    out["price_return"] = None
    out["dividend_return"] = None
    out["simple_return"] = None
    out["log_return"] = None
    return out


@pytest.fixture(autouse=True)
def stub_compute_returns(monkeypatch):
    monkeypatch.setattr(pipeline, "compute_returns", identity_compute_returns)


def test_calculate_returns_with_no_sources_is_nothing_to_do(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: [])

    result = pipeline.calculate_returns(conn=object())

    assert result["status"] == "nothing_to_do"
    assert result["rows_saved"] == 0
    assert "nothing to do" in caplog.text


def test_calculate_returns_processes_every_ticker_across_sources(monkeypatch):
    saved = []
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: ["yfinance"])
    monkeypatch.setattr(pipeline, "get_tickers", lambda conn, source: ["AAPL", "MSFT"])
    monkeypatch.setattr(
        pipeline,
        "iter_price_series",
        lambda conn, source, tickers, batch_size: iter([make_batch(tickers)]),
    )

    def fake_save(conn, source, df):
        saved.append((source, len(df)))
        return len(df)

    monkeypatch.setattr(pipeline, "save_market_returns", fake_save)

    result = pipeline.calculate_returns(conn=object())

    assert result["status"] == "ok"
    assert result["rows_saved"] == 2
    assert saved == [("yfinance", 2)]


def test_calculate_returns_processes_multiple_sources(monkeypatch):
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: ["yfinance", "other"])
    monkeypatch.setattr(pipeline, "get_tickers", lambda conn, source: [f"{source}-TICKER"])
    monkeypatch.setattr(
        pipeline,
        "iter_price_series",
        lambda conn, source, tickers, batch_size: iter([make_batch(tickers)]),
    )
    monkeypatch.setattr(pipeline, "save_market_returns", lambda conn, source, df: len(df))

    result = pipeline.calculate_returns(conn=object())

    assert result["status"] == "ok"
    assert result["rows_saved"] == 2
    assert result["tickers_processed"] == 2


def test_calculate_returns_isolates_a_failing_batch_from_the_rest(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: ["yfinance"])
    monkeypatch.setattr(pipeline, "get_tickers", lambda conn, source: ["AAPL", "MSFT"])
    monkeypatch.setattr(
        pipeline,
        "iter_price_series",
        lambda conn, source, tickers, batch_size: iter(
            [make_batch(["AAPL"]), make_batch(["MSFT"])]
        ),
    )

    def flaky_save(conn, source, df):
        if df["ticker"].iloc[0] == "AAPL":
            raise RuntimeError("numeric field overflow")
        return len(df)

    monkeypatch.setattr(pipeline, "save_market_returns", flaky_save)

    result = pipeline.calculate_returns(conn=object())

    assert result["status"] == "partial"
    assert result["rows_saved"] == 1  # MSFT's batch still saved
    assert result["batches_failed"] == 1
    assert "numeric field overflow" in caplog.text


def test_calculate_returns_skips_an_empty_batch_without_saving(monkeypatch):
    save_calls = []
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: ["yfinance"])
    monkeypatch.setattr(pipeline, "get_tickers", lambda conn, source: ["AAPL"])
    monkeypatch.setattr(
        pipeline,
        "iter_price_series",
        lambda conn, source, tickers, batch_size: iter([pd.DataFrame()]),
    )
    monkeypatch.setattr(
        pipeline, "save_market_returns", lambda conn, source, df: save_calls.append(df) or 0
    )

    result = pipeline.calculate_returns(conn=object())

    assert save_calls == []
    assert result["rows_saved"] == 0


def test_calculate_returns_only_tickers_filters_to_the_requested_tickers(monkeypatch):
    requested_tickers = []
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: ["yfinance"])
    monkeypatch.setattr(pipeline, "get_tickers", lambda conn, source: ["AAPL", "MSFT", "GOOG"])

    def fake_iter(conn, source, tickers, batch_size):
        requested_tickers.append(tickers)
        return iter([make_batch(tickers)])

    monkeypatch.setattr(pipeline, "iter_price_series", fake_iter)
    monkeypatch.setattr(pipeline, "save_market_returns", lambda conn, source, df: len(df))

    result = pipeline.calculate_returns(conn=object(), only_tickers={"AAPL"})

    assert requested_tickers == [["AAPL"]]
    assert result["status"] == "ok"
    assert result["tickers_processed"] == 1


def test_calculate_returns_unmatched_ticker_is_reported_as_partial(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: ["yfinance"])
    monkeypatch.setattr(pipeline, "get_tickers", lambda conn, source: ["AAPL"])
    monkeypatch.setattr(
        pipeline,
        "iter_price_series",
        lambda conn, source, tickers, batch_size: (
            iter([make_batch(tickers)]) if tickers else iter([])
        ),
    )
    monkeypatch.setattr(pipeline, "save_market_returns", lambda conn, source, df: len(df))

    result = pipeline.calculate_returns(conn=object(), only_tickers={"NOPE"})

    assert result["status"] == "partial"
    assert result["tickers_unmatched"] == 1
    assert "NOPE" in caplog.text


def test_calculate_returns_logs_a_completion_summary(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(pipeline, "get_sources", lambda conn: ["yfinance"])
    monkeypatch.setattr(pipeline, "get_tickers", lambda conn, source: ["AAPL"])
    monkeypatch.setattr(
        pipeline,
        "iter_price_series",
        lambda conn, source, tickers, batch_size: iter([make_batch(tickers)]),
    )
    monkeypatch.setattr(pipeline, "save_market_returns", lambda conn, source, df: len(df))

    pipeline.calculate_returns(conn=object())

    assert "calculate-returns complete" in caplog.text
