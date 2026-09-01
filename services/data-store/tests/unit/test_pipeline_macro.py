import logging
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from data_store.pipeline import calculate_macro_indicator_rates, ingest_macro_indicators

FRED_API_KEY = "key"  # pragma: allowlist secret


class FakeSource:
    def __init__(self, name, df=None, error=None):
        self.name = name
        self._df = df if df is not None else pd.DataFrame()
        self._error = error
        self.calls = []

    def fetch(self, regions):
        self.calls.append(regions)
        if self._error is not None:
            raise self._error
        return self._df


def make_raw_df(rows=1):
    return pd.DataFrame(
        [["US", "2024-01-01", "inflation", "monthly", 300.1, "Index"]] * rows,
        columns=["region", "observation_date", "indicator_type", "frequency", "value", "unit"],
    )


@pytest.fixture(autouse=True)
def caplog_info(caplog):
    caplog.set_level(logging.INFO)


def test_ingest_macro_indicators_fetches_fred_as_us_and_eurostat_as_configured():
    fred = FakeSource("fred", df=make_raw_df())
    eurostat = FakeSource("eurostat", df=make_raw_df())
    conn = MagicMock()

    with (
        patch(
            "data_store.pipeline.MACRO_INDICATOR_SOURCES",
            {"fred": lambda **_: fred, "eurostat": lambda **_: eurostat},
        ),
        patch("data_store.pipeline.save_raw_macro_indicators", return_value=1) as mock_save,
    ):
        ingest_macro_indicators(conn, fred_api_key=FRED_API_KEY, eu_regions=["EA", "DE"])

    assert fred.calls == [["US"]]
    assert eurostat.calls == [["EA", "DE"]]
    assert mock_save.call_count == 2


def test_ingest_macro_indicators_stamps_each_sources_own_name():
    fred = FakeSource("fred", df=make_raw_df())
    eurostat = FakeSource("eurostat", df=make_raw_df())
    conn = MagicMock()
    saved_frames = []

    with (
        patch(
            "data_store.pipeline.MACRO_INDICATOR_SOURCES",
            {"fred": lambda **_: fred, "eurostat": lambda **_: eurostat},
        ),
        patch(
            "data_store.pipeline.save_raw_macro_indicators",
            side_effect=lambda conn, df: saved_frames.append(df) or len(df),
        ),
    ):
        ingest_macro_indicators(conn, fred_api_key=FRED_API_KEY, eu_regions=["EA"])

    sources_seen = {df["source"].iloc[0] for df in saved_frames}
    assert sources_seen == {"fred", "eurostat"}


def test_ingest_macro_indicators_skips_eurostat_when_no_eu_regions_configured():
    fred = FakeSource("fred", df=make_raw_df())
    eurostat = FakeSource("eurostat")
    conn = MagicMock()

    with (
        patch(
            "data_store.pipeline.MACRO_INDICATOR_SOURCES",
            {"fred": lambda **_: fred, "eurostat": lambda **_: eurostat},
        ),
        patch("data_store.pipeline.save_raw_macro_indicators", return_value=1),
    ):
        ingest_macro_indicators(conn, fred_api_key=FRED_API_KEY, eu_regions=[])

    assert eurostat.calls == []
    assert fred.calls == [["US"]]


def test_ingest_macro_indicators_isolates_a_failing_source(caplog):
    fred = FakeSource("fred", error=RuntimeError("FRED is down"))
    eurostat = FakeSource("eurostat", df=make_raw_df())
    conn = MagicMock()

    with (
        patch(
            "data_store.pipeline.MACRO_INDICATOR_SOURCES",
            {"fred": lambda **_: fred, "eurostat": lambda **_: eurostat},
        ),
        patch("data_store.pipeline.save_raw_macro_indicators", return_value=1) as mock_save,
    ):
        result = ingest_macro_indicators(conn, fred_api_key=FRED_API_KEY, eu_regions=["EA"])

    assert mock_save.call_count == 1  # only eurostat's save happened
    assert result["status"] == "partial"
    assert result["sources_failed"] == 1
    assert "FRED is down" in caplog.text


def test_ingest_macro_indicators_returns_ok_status_with_saved_row_counts():
    fred = FakeSource("fred", df=make_raw_df(rows=2))
    eurostat = FakeSource("eurostat", df=make_raw_df(rows=3))
    conn = MagicMock()

    with (
        patch(
            "data_store.pipeline.MACRO_INDICATOR_SOURCES",
            {"fred": lambda **_: fred, "eurostat": lambda **_: eurostat},
        ),
        patch("data_store.pipeline.save_raw_macro_indicators", side_effect=[2, 3]),
    ):
        result = ingest_macro_indicators(conn, fred_api_key=FRED_API_KEY, eu_regions=["EA"])

    assert result["status"] == "ok"
    assert result["rows_saved"] == 5


def test_ingest_macro_indicators_handles_empty_fetch_result_without_saving():
    fred = FakeSource("fred", df=pd.DataFrame())
    eurostat = FakeSource("eurostat", df=pd.DataFrame())
    conn = MagicMock()

    with (
        patch(
            "data_store.pipeline.MACRO_INDICATOR_SOURCES",
            {"fred": lambda **_: fred, "eurostat": lambda **_: eurostat},
        ),
        patch("data_store.pipeline.save_raw_macro_indicators") as mock_save,
    ):
        result = ingest_macro_indicators(conn, fred_api_key=FRED_API_KEY, eu_regions=["EA"])

    mock_save.assert_not_called()
    assert result["rows_saved"] == 0


def test_calculate_macro_indicator_rates_logs_nothing_to_do_on_empty_raw_table():
    conn = MagicMock()

    with patch("data_store.pipeline.get_raw_indicators", return_value=pd.DataFrame()):
        result = calculate_macro_indicator_rates(conn)

    assert result["status"] == "nothing_to_do"
    assert result["rows_saved"] == 0


def test_calculate_macro_indicator_rates_computes_and_saves():
    conn = MagicMock()
    raw = pd.DataFrame(
        [["eurostat", "prc_hicp_manr", "EA", "2024-01-01", "inflation", "monthly", 2.8, "u"]],
        columns=[
            "source",
            "indicator_code",
            "region",
            "observation_date",
            "indicator_type",
            "frequency",
            "value",
            "unit",
        ],
    )

    with (
        patch("data_store.pipeline.get_raw_indicators", return_value=raw),
        patch("data_store.pipeline.save_macro_indicator_rates", return_value=1) as mock_save,
    ):
        result = calculate_macro_indicator_rates(conn)

    assert result["status"] == "ok"
    assert result["rows_saved"] == 1
    mock_save.assert_called_once()
