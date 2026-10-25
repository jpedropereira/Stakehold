"""The review fake's candidate ranking (RK-29), kept out of review_fakes.py, which is near the
repo's 64KB file limit; its Portfolio fake hands `rank_candidates` to `review_ranking` here.

Keyed by the weight, as the what-if's fake is keyed by the candidate: 5% is a full ranking
(long fund names, a gap-greyed row, a too-young and an unmeasurable ticker, three past the cap);
3% is the same without a risk-free rate; 2% has nothing to rank; 9% fails.
"""

from datetime import date
from decimal import Decimal

# (ticker, name, history start, Δσ_p, Δ diversification ratio, share of risk, ρ, ΔSharpe)
_CANDIDATES = (
    (
        "SGLD.L",
        "Invesco Physical Gold ETC",
        date(2009, 5, 26),
        -0.0071,
        0.0912,
        0.0123,
        -0.0512,
        0.0299,
    ),
    (
        "IEMA.AS",
        "iShares MSCI EM UCITS ETF (Acc)",
        date(2009, 9, 25),
        -0.0012,
        0.0344,
        0.0431,
        0.6812,
        -0.0121,
    ),
    (
        "EGLN.L",
        "iShares Physical Gold ETC",
        date(2011, 4, 8),
        -0.0069,
        0.0897,
        0.0131,
        -0.0421,
        0.0312,
    ),
    (
        "IUSN.DE",
        "iShares MSCI World Small Cap UCITS ETF",
        date(2018, 3, 30),
        0.0008,
        0.0122,
        0.0561,
        0.8912,
        0.0041,
    ),
    (
        "XGLE.DE",
        "Xtrackers II Eurozone Government Bond UCITS ETF",
        date(2009, 9, 25),
        -0.0094,
        0.1301,
        -0.0041,
        -0.1823,
        -0.0412,
    ),
    (
        "EUNA.AS",
        "iShares Core Global Aggregate Bond UCITS ETF",
        date(2017, 11, 24),
        -0.0088,
        0.1188,
        0.0012,
        0.0412,
        -0.0233,
    ),
    ("NVDA", "NVIDIA Corporation", date(2009, 9, 25), 0.0102, -0.0412, 0.1612, 0.7123, 0.0812),
)
_GAPPY = ("ZGAP.L", "A fund with missing weeks", date(2012, 1, 6), 0.0011)


def review_ranking(*, today, weight, window, exclude, assets, rank_by):
    from web.portfolio_client.base import (
        BacktestAssetStatus,
        CandidateRanking,
        RankedCandidate,
        RankingWindow,
        RiskFigure,
        UnrankedCandidate,
        WhatIfRow,
    )
    from web.portfolio_client.errors import PortfolioUnavailable

    if weight == Decimal("0.09"):
        raise PortfolioUnavailable("review: a 9% ranking fails on purpose")
    statuses = tuple(
        BacktestAssetStatus(t, "excluded" if t in exclude else "included", None) for t, _ in assets
    )
    years = {"3Y": 3, "5Y": 5, "10Y": 10, "Max": 17}[window]
    requested = date(today.year - years, today.month, today.day)
    start = max(requested, date(2019, 7, 29))
    weeks = (today - start).days // 7
    rf = weight != Decimal("0.03")
    common = dict(
        label="ex-ante · constant weights",
        rf_available=rf,
        statuses=statuses,
        universe=12,
        cap=50,
        weight=float(weight),
        rank_by=rank_by,
    )
    if weight == Decimal("0.02"):
        return CandidateRanking(
            **common,
            nothing="no tracked ticker outside the mix to rank",
            over_cap=(),
            window=None,
            ranked=None,
            greyed=None,
            unranked=(),
        )

    def figure(value, reason=None, se=None, flags=()):
        flags = (*flags, "insufficient_history") if reason else flags
        return RiskFigure(
            None if reason else value, start, today, "weekly", 52.0, weeks, 0, flags, reason, se
        )

    def row(before, after, se=None, reason=None, flags=()):
        b = None if before is None else figure(before, reason, se, flags)
        a = figure(after, reason, se, flags)
        delta = None if b is None or b.value is None or a.value is None else a.value - b.value
        return WhatIfRow(b, a, delta, 0.0412 if se and delta is not None else None)

    no_rf = "no risk-free rate data"

    def candidate(ticker, name, first, d_sigma, d_ratio, pcr, rho, d_sharpe, greyed=None):
        flags = ("data_gaps",) if ticker == "IUSN.DE" else ()
        sharpe = (
            row(0.8012, 0.8012 + d_sharpe, se=0.58)
            if rf
            else WhatIfRow(figure(None, no_rf), figure(None, no_rf), None, None)
        )
        return RankedCandidate(
            ticker,
            name,
            first,
            (today - first).days // 7,
            {
                "sigma_p": row(0.1412, 0.1412 + d_sigma, flags=flags),
                "diversification_ratio": row(1.16, 1.16 + d_ratio, reason=greyed),
                "candidate_pcr": row(None, pcr, reason=greyed),
                "candidate_correlation": row(None, rho, reason=greyed),
                "sharpe": sharpe,
            },
        )

    ranked = [candidate(*c) for c in _CANDIDATES]
    key = rank_by
    ranked.sort(key=lambda c: (c.rows[key].delta * (1 if key == "sigma_p" else -1), c.ticker))
    gappy = candidate(
        *_GAPPY, 0.0, 0.0, 0.0, 0.0, greyed=f"insufficient history (have {weeks - 60}, need 104)"
    )
    return CandidateRanking(
        **common,
        nothing=None,
        over_cap=("ZPRV.DE", "ZPRX.DE", "ZSIL.L"),
        window=RankingWindow(window, requested, start, today),
        ranked=tuple(ranked),
        greyed=(gappy,),
        unranked=(
            UnrankedCandidate(
                "WTAI.MI",
                "WisdomTree Artificial Intelligence UCITS ETF",
                "short_history",
                f"insufficient history (have 61, need {weeks})",
                date(2025, 8, 29),
                61,
            ),
            UnrankedCandidate(
                "0700.HK", "Tencent Holdings", "no_fx_rate", "no FX series for HKD", None, None
            ),
        ),
    )
