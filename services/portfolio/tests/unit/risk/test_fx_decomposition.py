"""risk/fx_decomposition.py: spec §6.4's FX decomposition of each non-EUR holding (RK-26), on
the listwise weekly panel the decomposition runs on (R12).

- Each row is the holding's local volatility, FX volatility, corr(r_local, r_fx) and EUR
  volatility, every one over the panel's weeks, so the EUR volatility is the decomposition's.
- Volatilities grey below 52 weeks, the correlation below 104 (§4.5).
- A EUR holding has no row: its r_fx is 0.

Calendar: 2015-01-02 is a Friday. Daily fixtures are every weekday after their base, with
returns from a fixed seed. A USD holding's daily FX return is built partly against its local
return, so the two are negatively correlated, and its EUR return is (1 + l)(1 + f) − 1 (§4.2).
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.ex_ante import decomposition_figures
from portfolio.risk.fx_decomposition import FxComponents, fx_decomposition
from portfolio.risk.listwise import listwise
from portfolio.risk.types import Flag, Frequency, ReturnSeries, Window
from portfolio.risk.weekly import to_weekly

FRIDAY = date(2015, 1, 2)
END = date(2021, 6, 30)  # a Wednesday


def _weekdays(base, end):
    dates, day = [], base
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            dates.append(day)
    return dates


def _series(base, dates, values):
    return ReturnSeries(Frequency.DAILY, base, tuple(dates), tuple(values))


def _usd(base=FRIDAY, end=END, seed=1, nulls=(), fx_scale=0.005, coupling=-0.3):
    """Daily (local, fx, eur) series. A NULL local day is NULL in EUR too; its FX is known."""
    dates = _weekdays(base, end)
    rng = np.random.default_rng(seed)
    local = rng.normal(0.0003, 0.01, len(dates))
    fx = coupling * local + rng.normal(0.0, fx_scale, len(dates))
    eur = (1 + local) * (1 + fx) - 1
    local_values = [None if d in nulls else float(v) for d, v in zip(dates, local)]
    eur_values = [None if d in nulls else float(v) for d, v in zip(dates, eur)]
    return (
        _series(base, dates, local_values),
        _series(base, dates, [float(v) for v in fx]),
        _series(base, dates, eur_values),
    )


def _eur(base=FRIDAY, end=END, seed=2):
    dates = _weekdays(base, end)
    values = np.random.default_rng(seed).normal(0.0003, 0.01, len(dates))
    return _series(base, dates, [float(v) for v in values])


def _window(start=FRIDAY, end=END):
    return Window("Max", start, end)


def _components(usd, currency="USD"):
    local, fx, _ = usd
    return FxComponents(currency, to_weekly(local), to_weekly(fx))


def _on_panel(series, panel):
    by_date = dict(zip(series.dates, series.values))
    return np.array([by_date[d] for d in panel.dates], dtype=float)


def test_a_usd_holding_gets_its_four_figures_over_the_panel_weeks():
    usd = _usd()
    weekly = {"US": to_weekly(usd[2]), "EU": to_weekly(_eur())}
    panel = listwise(weekly, _window())

    (row,) = fx_decomposition(panel, {"US": _components(usd)})

    local = _on_panel(to_weekly(usd[0]), panel)
    fx = _on_panel(to_weekly(usd[1]), panel)
    eur = _on_panel(weekly["US"], panel)
    assert row.ticker == "US"
    assert row.currency == "USD"
    assert row.local_volatility.value == pytest.approx(np.std(local, ddof=1) * np.sqrt(52))
    assert row.fx_volatility.value == pytest.approx(np.std(fx, ddof=1) * np.sqrt(52))
    assert row.eur_volatility.value == pytest.approx(np.std(eur, ddof=1) * np.sqrt(52))
    assert row.correlation.value == pytest.approx(np.corrcoef(local, fx)[0, 1])
    assert row.correlation.value < 0
    for figure in (row.local_volatility, row.fx_volatility, row.correlation, row.eur_volatility):
        assert (figure.window_start, figure.window_end) == (panel.window.start, panel.window.end)
        assert figure.frequency is Frequency.WEEKLY
        assert figure.q == 52
        assert figure.n_obs == panel.n_obs
        assert figure.n_gaps == panel.n_gaps


def test_the_weekly_components_compound_into_the_weekly_eur_return():
    """(1 + l)(1 + f) = 1 + e per day carries over to weeks, so the three figures describe one
    return. This is what lets the row take its EUR side from the panel."""
    local, fx, eur = (to_weekly(s) for s in _usd())

    assert local.dates == fx.dates == eur.dates
    combined = (1 + np.array(local.values)) * (1 + np.array(fx.values)) - 1
    assert combined == pytest.approx(np.array(eur.values), abs=1e-15)


def test_the_eur_volatility_is_the_decomposition_tables_volatility():
    usd = _usd()
    weekly = {"US": to_weekly(usd[2]), "EU": to_weekly(_eur())}
    window = _window()
    panel = listwise(weekly, window)

    (row,) = fx_decomposition(panel, {"US": _components(usd)})
    decomposition = decomposition_figures(weekly, {"US": 0.5, "EU": 0.5}, window)

    us = next(h for h in decomposition.holdings if h.key == "US")
    assert row.eur_volatility.value == pytest.approx(us.volatility, rel=1e-12)


def test_a_eur_holding_gets_no_row_and_an_all_eur_mix_gives_none():
    weekly = {"EU": to_weekly(_eur()), "EU2": to_weekly(_eur(seed=3))}
    panel = listwise(weekly, _window())

    assert fx_decomposition(panel, {}) == ()


def test_rows_follow_the_panels_order():
    first, second = _usd(seed=1), _usd(seed=5)
    weekly = {"B": to_weekly(second[2]), "EU": to_weekly(_eur()), "A": to_weekly(first[2])}
    panel = listwise(weekly, _window())

    rows = fx_decomposition(
        panel, {"A": _components(first, "GBP"), "B": _components(second)}
    )

    assert [(r.ticker, r.currency) for r in rows] == [("B", "USD"), ("A", "GBP")]


def test_between_52_and_104_weeks_the_volatilities_show_and_the_correlation_greys():
    end = FRIDAY + timedelta(weeks=80)
    usd = _usd(end=end)
    panel = listwise({"US": to_weekly(usd[2])}, _window(end=end))
    assert 52 <= panel.n_obs < 104

    (row,) = fx_decomposition(panel, {"US": _components(usd)})

    assert row.local_volatility.value is not None
    assert row.fx_volatility.value is not None
    assert row.eur_volatility.value is not None
    assert row.correlation.value is None
    assert row.correlation.reason.startswith("insufficient history")
    assert Flag.INSUFFICIENT_HISTORY in row.correlation.flags


def test_under_52_weeks_every_figure_greys():
    end = FRIDAY + timedelta(weeks=30)
    usd = _usd(end=end)
    panel = listwise({"US": to_weekly(usd[2])}, _window(end=end))

    (row,) = fx_decomposition(panel, {"US": _components(usd)})

    for figure in (row.local_volatility, row.fx_volatility, row.correlation, row.eur_volatility):
        assert figure.value is None
        assert figure.reason.startswith("insufficient history")


def test_a_late_holding_shortens_every_rows_window_to_the_panels():
    late = date(2017, 6, 2)  # a Friday
    early, young = _usd(seed=1), _usd(base=late, seed=4)
    weekly = {"US": to_weekly(early[2]), "YOUNG": to_weekly(young[2])}
    panel = listwise(weekly, _window())
    assert panel.limited_by == ("YOUNG",)

    rows = fx_decomposition(panel, {"US": _components(early), "YOUNG": _components(young)})

    for row in rows:
        assert row.eur_volatility.window_start == late
        assert row.local_volatility.n_obs == panel.n_obs


def test_a_week_another_holding_lost_is_dropped_from_every_row():
    """The listwise panel drops a week any holding is NULL on (R12); the FX rows do too, so the
    week counts as a gap rather than an FX-only observation."""
    null_day = date(2018, 3, 7)  # a Wednesday
    usd = _usd()
    other = _usd(seed=6, nulls={null_day})
    weekly = {"US": to_weekly(usd[2]), "OTHER": to_weekly(other[2])}
    panel = listwise(weekly, _window())

    rows = fx_decomposition(panel, {"US": _components(usd), "OTHER": _components(other)})

    assert date(2018, 3, 9) not in panel.dates
    for row in rows:
        assert row.fx_volatility.n_obs == panel.n_obs
        assert row.fx_volatility.n_gaps == panel.n_gaps >= 1


def test_a_pegged_currency_greys_the_correlation_rather_than_dividing_by_zero():
    usd = _usd(fx_scale=0.0, coupling=0.0)
    panel = listwise({"PEG": to_weekly(usd[2])}, _window())

    (row,) = fx_decomposition(panel, {"PEG": _components(usd, "DKK")})

    assert row.fx_volatility.value == 0.0
    assert row.correlation.value is None
    assert row.correlation.reason == "no variation in returns"


def test_a_holding_outside_the_panel_raises():
    usd = _usd()
    panel = listwise({"US": to_weekly(usd[2])}, _window())

    with pytest.raises(ValueError, match="NOPE"):
        fx_decomposition(panel, {"US": _components(usd), "NOPE": _components(usd)})


def test_a_component_missing_a_panel_week_raises():
    usd = _usd()
    panel = listwise({"US": to_weekly(usd[2])}, _window())
    local = to_weekly(usd[0])
    short = ReturnSeries(Frequency.WEEKLY, local.base_date, local.dates[:-5], local.values[:-5])

    with pytest.raises(ValueError, match="US"):
        fx_decomposition(panel, {"US": FxComponents("USD", short, to_weekly(usd[1]))})
