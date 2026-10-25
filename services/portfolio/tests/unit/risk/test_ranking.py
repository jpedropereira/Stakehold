"""risk/ranking.py: the candidate ranking mode (RK-29, spec §7.2).

- Every candidate is scored at one weight, funded pro-rata, on the holdings' own window: a
  candidate that doesn't cover it is listed unranked with R9's reason, never allowed to shorten
  the window for the others (agreed for RK-29, amending R10).
- Each scored row is exactly the what-if's (RK-19) for that candidate: its own listwise panel,
  the candidate at weight 0 for "before".
- The order is Δσ_p ascending, or Δ diversification ratio descending; ties by ticker. ΔSharpe is
  carried with its SE, never used to order. A row missing either key is listed greyed, last.

Calendar: 2015-01-02 is a Friday. Daily fixtures are every weekday after their base, with
returns from a fixed seed.
"""

from datetime import date, timedelta

import numpy as np
import pytest
from portfolio.risk.ex_ante_report import mix_report
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.ranking import (
    DIVERSIFICATION_RATIO,
    SIGMA_P,
    rank_candidates,
)
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.types import Frequency, ReturnSeries
from portfolio.risk.weekly import to_weekly
from portfolio.risk.what_if import PRO_RATA, what_if_report
from portfolio.risk.windows import StandardWindow

FRIDAY = date(2015, 1, 2)
END = date(2021, 6, 30)  # a Wednesday
FIRST_PUBLISHED = date(1999, 1, 4)
RATES = [
    RatePoint(FIRST_PUBLISHED, 1.0),
    RatePoint(date(2017, 1, 2), -0.4),
    RatePoint(date(2019, 6, 3), 0.5),
]
BEFORE = {"A": 0.6, "B": 0.4}


def _weekdays(base, end):
    dates, day = [], base
    while day < end:
        day += timedelta(days=1)
        if day.weekday() < 5:
            dates.append(day)
    return dates


def _daily(base, end, seed, nulls=(), drift=0.0003, scale=0.01):
    dates = _weekdays(base, end)
    values = list(np.random.default_rng(seed).normal(drift, scale, len(dates)))
    for k, day in enumerate(dates):
        if day in nulls:
            values[k] = None
    return ReturnSeries(Frequency.DAILY, base, tuple(dates), tuple(values))


def _against(series, beta, seed, drift=0.0, scale=0.004):
    """`beta` × `series` plus independent noise, on the same days."""
    noise = np.random.default_rng(seed).normal(drift, scale, len(series.dates))
    values = tuple(beta * v + n for v, n in zip(series.values, noise))
    return ReturnSeries(Frequency.DAILY, series.base_date, series.dates, values)


def _holdings():
    return {"A": _daily(FRIDAY, END, 1), "B": _daily(FRIDAY, END, 2, scale=0.02)}


def _candidates():
    holdings = _holdings()
    return {
        # Moves with the bigger holding: adds risk.
        "CLOSE": _against(holdings["B"], 1.0, 10, drift=0.003),
        # Moves against the portfolio: takes risk away.
        "HEDGE": _against(holdings["A"], -0.8, 11),
        # Unrelated: in between.
        "LONE": _daily(FRIDAY, END, 12, drift=0.0001, scale=0.012),
    }


def _currencies(*keys):
    return {key: "EUR" for key in keys}


def _rank(
    candidates=None,
    weight=0.05,
    label=StandardWindow.Y3,
    rank_by=SIGMA_P,
    rates=RATES,
    holdings=None,
):
    holdings = _holdings() if holdings is None else holdings
    candidates = _candidates() if candidates is None else candidates
    return rank_candidates(
        holdings,
        BEFORE,
        candidates,
        weight,
        label,
        rank_by,
        rates=rates,
        first_published=None if rates is None else FIRST_PUBLISHED,
    )


def _tickers(rows):
    return [row.ticker for row in rows]


# --- one row is the what-if ----------------------------------------------------------------------


@pytest.mark.parametrize("label", [StandardWindow.Y3, StandardWindow.Y5, StandardWindow.MAX])
def test_a_ranked_row_is_the_what_if_for_that_candidate(label):
    report = _rank(label=label)
    for row in report.ranked:
        daily = {**_holdings(), row.ticker: _candidates()[row.ticker]}
        what_if = what_if_report(
            daily,
            BEFORE,
            row.ticker,
            0.05,
            PRO_RATA,
            label,
            trading_days={key: set(series.dates) for key, series in daily.items()},
            currencies=_currencies("A", "B", row.ticker),
            rates=RATES,
            first_published=FIRST_PUBLISHED,
            survivorship=False,
        )
        assert row.sigma_p == what_if.sigma_p
        assert row.diversification_ratio == what_if.diversification_ratio
        assert row.candidate_pcr == what_if.candidate_pcr
        assert row.candidate_correlation == what_if.candidate_correlation
        assert row.sharpe == what_if.sharpe
        assert row.sharpe.delta_se is not None


def test_every_ranked_row_carries_its_history():
    report = _rank()
    hedge = next(row for row in report.ranked if row.ticker == "HEDGE")
    assert hedge.history_start == FRIDAY
    assert hedge.n_weeks == len(to_weekly(_candidates()["HEDGE"]).dates)


def test_the_report_names_the_request_and_the_holdings_own_window():
    report = _rank(weight=0.07, rank_by=DIVERSIFICATION_RATIO)
    assert (report.weight, report.rank_by) == (0.07, DIVERSIFICATION_RATIO)
    holdings = _holdings()
    alone = mix_report(
        holdings,
        BEFORE,
        StandardWindow.Y3,
        trading_days={key: set(series.dates) for key, series in holdings.items()},
        currencies=_currencies("A", "B"),
        rates=RATES,
        first_published=FIRST_PUBLISHED,
    )
    assert (report.requested, report.window) == (alone.requested, alone.window)


# --- order ---------------------------------------------------------------------------------------


def test_ranks_by_the_change_in_volatility_lowest_first():
    report = _rank()
    assert _tickers(report.ranked) == ["HEDGE", "LONE", "CLOSE"]
    deltas = [row.sigma_p.delta for row in report.ranked]
    assert deltas == sorted(deltas)
    assert deltas[0] < 0 < deltas[-1]


def test_ranks_by_the_change_in_diversification_ratio_highest_first():
    report = _rank(rank_by=DIVERSIFICATION_RATIO)
    deltas = [row.diversification_ratio.delta for row in report.ranked]
    assert deltas == sorted(deltas, reverse=True)
    assert report.ranked[0].ticker == "HEDGE"


def test_sharpe_never_sets_the_order():
    # CLOSE has the best historical return by far, so the best ΔSharpe, and still ranks last.
    report = _rank()
    by_sharpe = sorted(report.ranked, key=lambda row: row.sharpe.delta, reverse=True)
    assert by_sharpe[0].ticker == "CLOSE"
    assert report.ranked[-1].ticker == "CLOSE"


def test_ties_are_broken_by_ticker():
    lone = _candidates()["LONE"]
    report = _rank(candidates={"ZED": lone, "ACE": lone})
    assert _tickers(report.ranked) == ["ACE", "ZED"]


# --- what isn't ranked ---------------------------------------------------------------------------


def test_a_candidate_starting_after_the_window_is_unranked_with_its_weeks():
    late = _daily(date(2020, 1, 3), END, 13)  # a Friday, 18 months before the end
    report = _rank(candidates={**_candidates(), "LATE": late})
    assert "LATE" not in _tickers(report.ranked)
    (unranked,) = report.unranked
    assert unranked.ticker == "LATE"
    assert unranked.history_start == date(2020, 1, 3)
    assert unranked.n_weeks == len(to_weekly(late).dates)
    assert unranked.reason.startswith("insufficient history (have ")
    have, need = (
        int(part)
        for part in unranked.reason[len("insufficient history (have ") : -1].split(", need ")
    )
    assert have < need
    # It didn't shorten the window the others were measured on.
    assert report.window == _rank().window


def test_a_candidate_ending_before_the_window_is_unranked():
    stale = _daily(FRIDAY, date(2021, 3, 31), 14)
    report = _rank(candidates={**_candidates(), "STALE": stale})
    assert _tickers(report.unranked) == ["STALE"]
    assert report.unranked[0].reason.startswith("insufficient history")
    assert report.window == _rank().window


def test_a_candidate_whose_gaps_grey_a_key_is_listed_greyed_after_the_ranking():
    # A NULL Wednesday in 60 of the window's ~156 weeks: σ_p (52 weeks) is still measured, the
    # diversification ratio (104) isn't.
    wednesdays = [date(2019, 1, 2) + timedelta(weeks=k) for k in range(60)]
    gappy = _daily(FRIDAY, END, 15, nulls=set(wednesdays))
    report = _rank(candidates={**_candidates(), "GAPPY": gappy})
    assert "GAPPY" not in _tickers(report.ranked)
    (greyed,) = report.greyed
    assert greyed.ticker == "GAPPY"
    assert greyed.sigma_p.delta is not None
    assert greyed.diversification_ratio.delta is None
    assert greyed.diversification_ratio.after.reason.startswith("insufficient history")


def test_unranked_and_greyed_lists_keep_ticker_order():
    late = _daily(date(2020, 1, 3), END, 13)
    report = _rank(candidates={"ZLATE": late, "ALATE": late, **_candidates()})
    assert _tickers(report.unranked) == ["ALATE", "ZLATE"]


def test_no_candidates_gives_an_empty_report():
    report = _rank(candidates={})
    assert (report.ranked, report.greyed, report.unranked) == ((), (), ())
    assert report.window is not None


def test_without_a_risk_free_series_only_sharpe_is_greyed():
    report = _rank(rates=None)
    assert _tickers(report.ranked) == ["HEDGE", "LONE", "CLOSE"]
    for row in report.ranked:
        assert row.sharpe.delta is None
        assert row.sharpe.delta_se is None
        assert row.sharpe.after.reason == NO_RISK_FREE
        assert row.sigma_p.delta is not None


# --- refused requests ----------------------------------------------------------------------------


def test_one_year_is_refused():
    # The diversification ratio needs 104 weeks, so every row would be greyed.
    with pytest.raises(ValueError, match="1Y"):
        _rank(label=StandardWindow.Y1)


def test_an_unknown_rank_key_is_refused():
    with pytest.raises(ValueError, match="sharpe"):
        _rank(rank_by="sharpe")


@pytest.mark.parametrize("weight", [0.0, 1.0, -0.1])
def test_a_weight_outside_zero_to_one_is_refused(weight):
    with pytest.raises(ValueError, match="weight"):
        _rank(weight=weight)


def test_a_holding_as_candidate_is_refused():
    with pytest.raises(ValueError, match="already a holding"):
        _rank(candidates={"A": _holdings()["A"]})
