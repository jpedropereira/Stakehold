from datetime import date

import pandas as pd
import pytest
from data_store.macro.fred_source import FRED_SERIES, FredMacroIndicatorSource


class FakeFred:
    def __init__(self, *, series=None, errors=None, error_counts=None):
        self.series = series or {}
        self.errors = errors or {}
        self.error_counts = dict(error_counts or {})
        self.calls = []

    def get_series(self, series_id):
        self.calls.append(series_id)
        if series_id in self.error_counts:
            if self.error_counts[series_id] > 0:
                self.error_counts[series_id] -= 1
                raise self.errors[series_id]
            return self.series[series_id]
        if series_id in self.errors:
            raise self.errors[series_id]
        return self.series[series_id]


def make_source(fake_fred, **kwargs):
    source = FredMacroIndicatorSource(api_key="test-key", **kwargs)
    source._client = fake_fred
    return source


def test_name_is_fred():
    assert FredMacroIndicatorSource.name == "fred"


def test_fetch_rejects_non_us_regions():
    source = make_source(FakeFred())

    with pytest.raises(ValueError, match="US"):
        source.fetch(["EA"])


def test_fetch_reshapes_both_series_into_long_form_rows():
    cpi = pd.Series([300.1, 300.5], index=pd.to_datetime(["2024-01-01", "2024-02-01"]))
    gdp = pd.Series([22000.0], index=pd.to_datetime(["2024-01-01"]))
    fake = FakeFred(series={"CPIAUCSL": cpi, "GDPC1": gdp})

    result = make_source(fake).fetch(["US"])

    assert set(result["indicator_code"]) == {"CPIAUCSL", "GDPC1"}
    assert (result["region"] == "US").all()
    cpi_rows = result[result["indicator_code"] == "CPIAUCSL"]
    assert list(cpi_rows["observation_date"]) == [date(2024, 1, 1), date(2024, 2, 1)]
    assert list(cpi_rows["value"]) == [300.1, 300.5]
    assert (cpi_rows["indicator_type"] == "inflation").all()
    assert (cpi_rows["frequency"] == "monthly").all()
    gdp_rows = result[result["indicator_code"] == "GDPC1"]
    assert (gdp_rows["indicator_type"] == "growth").all()
    assert (gdp_rows["frequency"] == "quarterly").all()


def test_fetch_stamps_the_configured_unit_for_each_series():
    cpi = pd.Series([300.1], index=pd.to_datetime(["2024-01-01"]))
    gdp = pd.Series([22000.0], index=pd.to_datetime(["2024-01-01"]))
    fake = FakeFred(series={"CPIAUCSL": cpi, "GDPC1": gdp})

    result = make_source(fake).fetch(["US"])

    cpi_unit = result[result["indicator_code"] == "CPIAUCSL"]["unit"].iloc[0]
    gdp_unit = result[result["indicator_code"] == "GDPC1"]["unit"].iloc[0]
    assert cpi_unit == FRED_SERIES["CPIAUCSL"]["unit"]
    assert gdp_unit == FRED_SERIES["GDPC1"]["unit"]


def test_fetch_drops_nan_observations():
    # fredapi turns FRED's "." missing-observation marker into NaN and leaves it in the series
    # -- confirmed against the installed fredapi source. Must never land in raw_macro_indicators'
    # NOT NULL value column.
    cpi = pd.Series(
        [300.1, float("nan"), 300.5],
        index=pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
    )
    fake = FakeFred(series={"CPIAUCSL": cpi, "GDPC1": pd.Series(dtype=float)})

    result = make_source(fake).fetch(["US"])

    cpi_rows = result[result["indicator_code"] == "CPIAUCSL"]
    assert list(cpi_rows["observation_date"]) == [date(2024, 1, 1), date(2024, 3, 1)]
    assert list(cpi_rows["value"]) == [300.1, 300.5]


def test_fetch_returns_empty_frame_when_both_series_fail(caplog):
    caplog.set_level("WARNING")
    fake = FakeFred(errors={"CPIAUCSL": ValueError("No data exists"), "GDPC1": ValueError("boom")})

    result = make_source(fake).fetch(["US"])

    assert result.empty
    assert list(result.columns) == [
        "indicator_code",
        "region",
        "observation_date",
        "indicator_type",
        "frequency",
        "value",
        "unit",
    ]


def test_a_failing_series_does_not_cost_the_other_series(caplog):
    caplog.set_level("WARNING")
    cpi = pd.Series([300.1], index=pd.to_datetime(["2024-01-01"]))
    fake = FakeFred(series={"CPIAUCSL": cpi}, errors={"GDPC1": ValueError("No data exists")})

    result = make_source(fake).fetch(["US"])

    assert list(result["indicator_code"].unique()) == ["CPIAUCSL"]
    assert "GDPC1" in caplog.text


def test_transient_error_is_retried_then_succeeds():
    cpi = pd.Series([300.1], index=pd.to_datetime(["2024-01-01"]))
    fake = FakeFred(
        series={"CPIAUCSL": cpi, "GDPC1": cpi},
        errors={"CPIAUCSL": ValueError("Too Many Requests")},
        error_counts={"CPIAUCSL": 1},
    )
    source = make_source(fake, max_retries=3, backoff_base_seconds=0.0)

    result = source.fetch(["US"])

    assert fake.calls.count("CPIAUCSL") == 2  # one failure, one retry that succeeds
    assert "CPIAUCSL" in set(result["indicator_code"])


def test_non_transient_error_is_not_retried(caplog):
    caplog.set_level("WARNING")
    fake = FakeFred(errors={"CPIAUCSL": ValueError("No data exists for series id: CPIAUCSL")})
    source = make_source(fake, max_retries=3, backoff_base_seconds=0.0)

    source.fetch(["US"])

    assert fake.calls.count("CPIAUCSL") == 1
