"""The regime view's review data (RK-28), split out of review_fakes.py to keep it under the
repo's 64KB file limit. Real regimes from data-store's table since 2007, each with made-up
figures: a section's view is every one overlapping its history, clipped to it, as portfolio's
risk/regimes.py does. Two benchmarks stand for data-store's failure modes: REGIMES.FAIL (the
fetch failed) and REGIMES.NONE (no regimes loaded).
"""

from datetime import date

FAIL = "REGIMES.FAIL"
NONE_LOADED = "REGIMES.NONE"

# (id, dimension, scope, name, start, end, severity, return, max drawdown, benchmark's return,
# benchmark's max drawdown).
_REGIMES = (
    (
        "gfc",
        "drawdown",
        "SPX",
        "Global financial crisis",
        date(2007, 10, 9),
        date(2009, 3, 9),
        "extreme",
        -0.5123,
        -0.5641,
        -0.4877,
        -0.5512,
    ),
    (
        "us_rec_2007",
        "business_cycle",
        "US",
        "Great Recession",
        date(2007, 12, 1),
        date(2009, 6, 30),
        "severe",
        -0.3011,
        -0.5312,
        -0.2876,
        -0.5233,
    ),
    (
        "ea_debt_2010",
        "event",
        "EA",
        "Euro area debt crisis",
        date(2009, 10, 1),
        date(2012, 9, 6),
        "severe",
        0.0812,
        -0.2214,
        0.0533,
        -0.2402,
    ),
    (
        "oil_collapse_2014",
        "commodity",
        "GLOBAL",
        "Oil price collapse 2014-16",
        date(2014, 6, 20),
        date(2016, 2, 11),
        "severe",
        -0.0423,
        -0.1876,
        -0.0611,
        -0.1932,
    ),
    (
        "china_deval_2015",
        "event",
        "GLOBAL",
        "China devaluation shock",
        date(2015, 8, 11),
        date(2016, 2, 11),
        "moderate",
        -0.1102,
        -0.1544,
        -0.1217,
        -0.1601,
    ),
    (
        "spx_corr_2018",
        "drawdown",
        "SPX",
        "Q4 2018 selloff",
        date(2018, 9, 20),
        date(2018, 12, 24),
        "moderate",
        -0.1356,
        -0.1622,
        -0.1288,
        -0.1574,
    ),
    (
        "spx_bear_2020",
        "drawdown",
        "SPX",
        "COVID crash",
        date(2020, 2, 19),
        date(2020, 3, 23),
        "severe",
        -0.3105,
        -0.3311,
        -0.3247,
        -0.3389,
    ),
    (
        "ea_rec_2020",
        "business_cycle",
        "EA",
        "Euro area COVID recession",
        date(2020, 1, 1),
        date(2020, 6, 30),
        "severe",
        -0.0688,
        -0.3311,
        -0.0912,
        -0.3389,
    ),
    (
        "covid_pandemic",
        "event",
        "GLOBAL",
        "COVID-19 pandemic",
        date(2020, 3, 11),
        date(2023, 5, 5),
        "extreme",
        0.4121,
        -0.2107,
        0.3812,
        -0.2244,
    ),
    (
        "us_infl_2021",
        "inflation",
        "US",
        "Post-COVID inflation",
        date(2021, 4, 1),
        date(2023, 6, 30),
        "severe",
        0.0911,
        -0.2107,
        0.0702,
        -0.2244,
    ),
    (
        "spx_bear_2022",
        "drawdown",
        "SPX",
        "2022 bear market",
        date(2022, 1, 3),
        date(2022, 10, 12),
        "severe",
        -0.1844,
        -0.2107,
        -0.1702,
        -0.2015,
    ),
    (
        "ukraine_war",
        "event",
        "GLOBAL",
        "Russian invasion of Ukraine",
        date(2022, 2, 24),
        None,
        "extreme",
        0.4317,
        -0.1873,
        0.4022,
        -0.1795,
    ),
    (
        "ecb_hike_2022",
        "monetary_policy",
        "EA",
        "ECB hiking cycle 2022-23",
        date(2022, 7, 27),
        date(2023, 9, 19),
        "extreme",
        0.1212,
        -0.1188,
        0.1105,
        -0.1132,
    ),
    (
        "svb_crisis",
        "event",
        "US",
        "US regional banking crisis",
        date(2023, 3, 8),
        date(2023, 5, 1),
        "severe",
        0.0211,
        -0.0644,
        0.0187,
        -0.0702,
    ),
    (
        "ecb_hold_2023",
        "monetary_policy",
        "EA",
        "ECB on hold 2023-24",
        date(2023, 9, 20),
        date(2024, 6, 11),
        "moderate",
        0.1534,
        -0.0712,
        0.1488,
        -0.0688,
    ),
    (
        "ecb_cut_2024",
        "monetary_policy",
        "EA",
        "ECB easing cycle",
        date(2024, 6, 12),
        None,
        "moderate",
        0.1802,
        -0.1873,
        0.1755,
        -0.1795,
    ),
    (
        "yen_carry_unwind",
        "event",
        "GLOBAL",
        "Yen carry trade unwind",
        date(2024, 8, 5),
        date(2024, 8, 9),
        "moderate",
        0.0133,
        -0.0211,
        0.0117,
        -0.0198,
    ),
    (
        "spx_corr_2025",
        "drawdown",
        "SPX",
        "2025 tariff selloff",
        date(2025, 2, 19),
        date(2025, 4, 8),
        "moderate",
        -0.1478,
        -0.1873,
        -0.1402,
        -0.1795,
    ),
)


def review_regimes(first, last, benchmark, benchmark_first, *, scale=1.0):
    """The view of a series from `first` to `last` against `benchmark` (first day
    `benchmark_first`, None when it isn't tracked). `scale` sets ex-ante's figures apart from
    ex-post's."""
    from web.portfolio_client.base import (
        RegimeFigures,
        RegimeRow,
        RegimeView,
        RiskFigure,
    )

    if benchmark == FAIL:
        return RegimeView(False, "regimes unavailable: data-store call failed", None, ())
    if benchmark == NONE_LOADED:
        return RegimeView(False, "no regimes loaded", 0, ())

    def figure(value, start, end, flags=(), reason=None):
        n_obs = max(1, round((end - start).days / 1.45))
        if reason:
            value, n_obs, flags = None, 0, ("insufficient_history",)
        elif flags and n_obs >= 200:
            flags = ()
        return RiskFigure(value, start, end, "daily", 251.6, n_obs, 0, flags, reason, None)

    rows = []
    for rid, dim, scope, name, start, end, severity, cr, mdd, b_cr, b_mdd in _REGIMES:
        m_start, m_end = max(start, first), min(end or last, last)
        if m_start >= m_end:
            continue
        if benchmark_first is None:
            reason = "benchmark unavailable: not tracked"
        elif benchmark_first > m_start:
            reason = f"benchmark history too short (from {benchmark_first})"
        else:
            reason = None
        short = ("short_regime",)
        rows.append(
            RegimeRow(
                rid,
                dim,
                scope,
                name,
                severity,
                "manual",
                start,
                end,
                m_start,
                m_end,
                (m_start, m_end) != (start, end or m_end),
                end is None,
                RegimeFigures(
                    figure(round(cr * scale, 4), m_start, m_end),
                    figure(round(mdd * scale, 4), m_start, m_end, short),
                ),
                RegimeFigures(
                    figure(b_cr, m_start, m_end, reason=reason),
                    figure(b_mdd, m_start, m_end, short, reason=reason),
                ),
            )
        )
    return RegimeView(True, None, 94, tuple(rows))
