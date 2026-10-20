"""Tests for pipeline.ingest_risk_free_rates: which series it fetches, over which window, and how
it reports a skipped or failed series. The source and repository are faked; the ECB parsing is
tested in risk_free/test_ecb_source.py and the upsert in the integration suite."""

import logging
from datetime import date

import pandas as pd
import pytest
from data_store import pipeline

START = date(2026, 10, 13)
END = date(2026, 10, 20)
CATCH_UP_LIMIT = date(2025, 10, 19)


class FakeRiskFreeRateSource:
    name = "ecb"
    series = ("estr", "eonia")
    discontinued = {"eonia": date(2021, 12, 31)}

    def __init__(self, *, failing=(), empty=()):
        self.failing = set(failing)
        self.empty = set(empty)
        self.fetch_calls = []

    def fetch(self, series, start_date, end_date):
        self.fetch_calls.append((series, start_date, end_date))
        if series in self.failing:
            raise RuntimeError(f"ecb: {series} could not be fetched")
        if series in self.empty:
            return pd.DataFrame(columns=["series", "rate_date", "rate"])
        return pd.DataFrame(
            {"series": [series, series], "rate_date": [start_date, end_date], "rate": [1.9, 1.9]}
        )


@pytest.fixture
def saves(monkeypatch):
    saved = []
    monkeypatch.setattr(
        pipeline,
        "save_risk_free_rates",
        lambda conn, source, df: saved.append((source, df["series"].iloc[0], len(df))) or len(df),
    )
    return saved


def run(monkeypatch, source, latest, **kwargs):
    monkeypatch.setitem(pipeline.RISK_FREE_RATE_SOURCES, "ecb", lambda: source)
    monkeypatch.setattr(
        pipeline, "get_latest_risk_free_dates", lambda conn, source_name, series: dict(latest)
    )
    return pipeline.ingest_risk_free_rates(
        object(), START, END, catch_up_limit=kwargs.get("catch_up_limit", CATCH_UP_LIMIT)
    )


def test_first_run_fetches_every_series_whole_history(monkeypatch, saves):
    source = FakeRiskFreeRateSource()

    result = run(monkeypatch, source, latest={})

    assert sorted(source.fetch_calls) == [
        ("eonia", pipeline.UNBOUNDED_EARLIEST_DATE, END),
        ("estr", pipeline.UNBOUNDED_EARLIEST_DATE, END),
    ]
    assert result == {
        "status": "ok",
        "rows_saved": 4,
        "series_fetched": 2,
        "series_skipped": 0,
        "series_failed": 0,
    }


def test_a_stored_live_series_is_fetched_over_the_window(monkeypatch, saves):
    source = FakeRiskFreeRateSource()

    run(monkeypatch, source, latest={"estr": date(2026, 10, 19), "eonia": date(2021, 12, 31)})

    assert source.fetch_calls == [("estr", START, END)]
    assert saves == [("ecb", "estr", 2)]


def test_a_stored_discontinued_series_is_skipped_and_says_so(monkeypatch, saves, caplog):
    caplog.set_level(logging.INFO)
    source = FakeRiskFreeRateSource()

    result = run(
        monkeypatch, source, latest={"estr": date(2026, 10, 19), "eonia": date(2021, 12, 31)}
    )

    assert "eonia" not in [call[0] for call in source.fetch_calls]
    assert "eonia was discontinued on 2021-12-31 and is stored through 2021-12-31" in caplog.text
    assert result["series_skipped"] == 1
    assert result["status"] == "ok"


def test_a_live_series_behind_the_window_catches_up_from_its_last_date(monkeypatch, saves):
    source = FakeRiskFreeRateSource()

    run(monkeypatch, source, latest={"estr": date(2026, 9, 1), "eonia": date(2021, 12, 31)})

    assert source.fetch_calls == [("estr", date(2026, 9, 1), END)]


def test_one_series_failing_is_partial_and_keeps_the_other(monkeypatch, saves, caplog):
    source = FakeRiskFreeRateSource(failing={"eonia"})

    result = run(monkeypatch, source, latest={})

    assert saves == [("ecb", "estr", 2)]
    assert result["status"] == "partial"
    assert result["series_failed"] == 1
    assert result["rows_saved"] == 2
    assert "ingest-risk-free-rates: eonia failed, skipping" in caplog.text


def test_a_save_failure_counts_as_that_series_failing(monkeypatch, caplog):
    def save(conn, source, df):
        raise RuntimeError("connection lost")

    monkeypatch.setattr(pipeline, "save_risk_free_rates", save)
    source = FakeRiskFreeRateSource()

    result = run(monkeypatch, source, latest={"eonia": date(2021, 12, 31)})

    assert result["status"] == "partial"
    assert result["series_failed"] == 1
    assert "estr failed, skipping" in caplog.text


def test_an_empty_response_is_logged_not_failed(monkeypatch, saves, caplog):
    caplog.set_level(logging.INFO)
    source = FakeRiskFreeRateSource(empty={"estr"})

    result = run(
        monkeypatch, source, latest={"estr": date(2026, 10, 19), "eonia": date(2021, 1, 1)}
    )

    assert f"estr -> no data ({START} to {END})" in caplog.text
    assert result["series_failed"] == 0
    assert result["rows_saved"] == 0


def test_logs_a_start_line_each_series_and_a_summary(monkeypatch, saves, caplog):
    caplog.set_level(logging.INFO)
    source = FakeRiskFreeRateSource()

    run(monkeypatch, source, latest={"estr": date(2026, 10, 19), "eonia": date(2021, 12, 31)})

    assert "ingest-risk-free-rates: checking 2 series (source=ecb)." in caplog.text
    assert f"ingest-risk-free-rates: estr -> 2 row(s) ({START} to {END})" in caplog.text
    assert (
        "ingest-risk-free-rates complete: 2 row(s) saved; 1 series fetched, "
        "1 skipped, 0 failed." in caplog.text
    )
