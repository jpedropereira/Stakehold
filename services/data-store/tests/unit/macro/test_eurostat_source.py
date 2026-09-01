from datetime import date

import pandas as pd
import pytest
from data_store.macro.eurostat_source import EurostatMacroIndicatorSource, _parse_period


def hicp_frame(rows):
    """rows: list of (geo, {period: value}). Mirrors the real eurostat package's shape --
    dimension columns plus 'geo\\TIME_PERIOD' plus one column per time period -- confirmed
    against a live call (see eurostat_source.py's module docstring comment)."""
    records = []
    periods = set()
    for geo, values in rows:
        record = {"freq": "M", "unit": "RCH_A", "coicop": "CP00", "geo\\TIME_PERIOD": geo}
        record.update(values)
        periods.update(values.keys())
        records.append(record)
    return pd.DataFrame(
        records, columns=["freq", "unit", "coicop", "geo\\TIME_PERIOD", *sorted(periods)]
    )


def gdp_frame(rows):
    records = []
    periods = set()
    for geo, values in rows:
        record = {
            "freq": "Q",
            "unit": "CLV_PCH_SM",
            "s_adj": "SCA",
            "na_item": "B1GQ",
            "geo\\TIME_PERIOD": geo,
        }
        record.update(values)
        periods.update(values.keys())
        records.append(record)
    return pd.DataFrame(
        records, columns=["freq", "unit", "s_adj", "na_item", "geo\\TIME_PERIOD", *sorted(periods)]
    )


class FakeEurostat:
    def __init__(self, *, responses=None, errors=None, error_counts=None):
        self.responses = responses or {}
        self.errors = errors or {}
        self.error_counts = dict(error_counts or {})
        self.calls = []

    def get_data_df(self, code, flags, filter_pars):
        self.calls.append((code, filter_pars))
        if code in self.error_counts:
            if self.error_counts[code] > 0:
                self.error_counts[code] -= 1
                raise self.errors[code]
            return self.responses.get(code)
        if code in self.errors:
            raise self.errors[code]
        return self.responses.get(code)


def make_source(fake, monkeypatch, **kwargs):
    monkeypatch.setattr("data_store.macro.eurostat_source.eurostat", fake)
    return EurostatMacroIndicatorSource(**kwargs)


def test_name_is_eurostat():
    assert EurostatMacroIndicatorSource.name == "eurostat"


def test_fetch_rejects_no_regions():
    source = EurostatMacroIndicatorSource()

    with pytest.raises(ValueError, match="No regions"):
        source.fetch([])


def test_fetch_reshapes_both_datasets_into_long_form_rows(monkeypatch):
    fake = FakeEurostat(
        responses={
            "prc_hicp_manr": hicp_frame([("EA20", {"2024-01": 2.8, "2024-02": 2.6})]),
            "namq_10_gdp": gdp_frame([("EA20", {"2024-Q1": 0.7})]),
        }
    )
    source = make_source(fake, monkeypatch)

    result = source.fetch(["EA"])

    assert set(result["indicator_code"]) == {"prc_hicp_manr", "namq_10_gdp"}
    assert (result["region"] == "EA").all()  # mapped back from Eurostat's EA20 geo code
    hicp_rows = result[result["indicator_code"] == "prc_hicp_manr"].sort_values("observation_date")
    assert list(hicp_rows["observation_date"]) == [date(2024, 1, 1), date(2024, 2, 1)]
    assert list(hicp_rows["value"]) == [2.8, 2.6]
    assert (hicp_rows["indicator_type"] == "inflation").all()
    gdp_rows = result[result["indicator_code"] == "namq_10_gdp"]
    assert list(gdp_rows["observation_date"]) == [date(2024, 1, 1)]
    assert (gdp_rows["indicator_type"] == "growth").all()


def test_fetch_maps_our_ea_alias_to_eurostats_ea20_geo_code(monkeypatch):
    fake = FakeEurostat(responses={"prc_hicp_manr": hicp_frame([]), "namq_10_gdp": gdp_frame([])})
    source = make_source(fake, monkeypatch)

    source.fetch(["EA", "DE"])

    _, filter_pars = fake.calls[0]
    assert filter_pars["geo"] == ["EA20", "DE"]


def test_fetch_skips_a_geo_row_not_in_the_requested_regions(monkeypatch):
    # A defensive case -- the request is filtered by geo, so this shouldn't normally happen, but
    # a row for an unrequested region must never leak through as an unmapped/garbage region.
    fake = FakeEurostat(
        responses={
            "prc_hicp_manr": hicp_frame([("EA20", {"2024-01": 2.8}), ("XX", {"2024-01": 9.9})]),
            "namq_10_gdp": gdp_frame([]),
        }
    )
    source = make_source(fake, monkeypatch)

    result = source.fetch(["EA"])

    assert set(result["region"]) == {"EA"}


def test_fetch_drops_missing_values(monkeypatch):
    fake = FakeEurostat(
        responses={
            "prc_hicp_manr": hicp_frame([("EA20", {"2024-01": 2.8, "2024-02": float("nan")})]),
            "namq_10_gdp": gdp_frame([]),
        }
    )
    source = make_source(fake, monkeypatch)

    result = source.fetch(["EA"])

    assert list(result["observation_date"]) == [date(2024, 1, 1)]


def test_fetch_returns_empty_frame_when_the_api_returns_none(monkeypatch):
    # get_data_df returns None (not an empty DataFrame) when a filter matches nothing --
    # confirmed against the installed eurostat package's own get_data/get_data_df source.
    fake = FakeEurostat(responses={"prc_hicp_manr": None, "namq_10_gdp": None})
    source = make_source(fake, monkeypatch)

    result = source.fetch(["EA"])

    assert result.empty


def test_a_failing_dataset_does_not_cost_the_other_dataset(monkeypatch, caplog):
    caplog.set_level("WARNING")
    fake = FakeEurostat(
        responses={"prc_hicp_manr": hicp_frame([("EA20", {"2024-01": 2.8})])},
        errors={"namq_10_gdp": ValueError("boom")},
    )
    source = make_source(fake, monkeypatch)

    result = source.fetch(["EA"])

    assert list(result["indicator_code"].unique()) == ["prc_hicp_manr"]
    assert "namq_10_gdp" in caplog.text


def test_transient_error_is_retried_then_succeeds(monkeypatch):
    fake = FakeEurostat(
        responses={
            "prc_hicp_manr": hicp_frame([("EA20", {"2024-01": 2.8})]),
            "namq_10_gdp": gdp_frame([]),
        },
        errors={"prc_hicp_manr": ValueError("Connection reset")},
        error_counts={"prc_hicp_manr": 1},
    )
    source = make_source(fake, monkeypatch, max_retries=3, backoff_base_seconds=0.0)

    result = source.fetch(["EA"])

    assert sum(1 for code, _ in fake.calls if code == "prc_hicp_manr") == 2  # one retry
    assert "prc_hicp_manr" in set(result["indicator_code"])


def test_missing_geo_column_is_skipped_with_a_warning(monkeypatch, caplog):
    caplog.set_level("WARNING")
    frame_without_geo = pd.DataFrame({"freq": ["M"], "unit": ["RCH_A"], "2024-01": [2.8]})
    fake = FakeEurostat(
        responses={"prc_hicp_manr": frame_without_geo, "namq_10_gdp": gdp_frame([])}
    )
    source = make_source(fake, monkeypatch)

    result = source.fetch(["EA"])

    assert result.empty
    assert "geo" in caplog.text


@pytest.mark.parametrize(
    "label,expected",
    [
        ("2024-01", date(2024, 1, 1)),
        ("2024-12", date(2024, 12, 1)),
        ("2024-Q1", date(2024, 1, 1)),
        ("2024-Q4", date(2024, 10, 1)),
        ("2024", date(2024, 1, 1)),
    ],
)
def test_parse_period_handles_every_confirmed_format(label, expected):
    assert _parse_period(label) == expected


def test_parse_period_returns_none_for_garbage():
    assert _parse_period("not-a-period") is None
