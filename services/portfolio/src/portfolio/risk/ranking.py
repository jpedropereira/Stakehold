"""The candidate ranking mode (RK-29, spec §7.2): many candidates scored at one weight, funded
pro-rata, against today's mix. Pure, like what_if.py: risk/flows.py picks the candidates, fetches
them and normalises the holdings' weights.

- **The window is the holdings'**, ending on their latest common date (agreed for RK-29). A
  candidate that doesn't cover it, starting later or ending earlier, would shorten it: it is
  listed unranked with R9's reason, counted in weeks, so one young ticker can't shrink the
  window every other candidate is measured on. Among the ranked ones the window is common, as
  R10 asks (the spec's R10 amendment records this).
- **Each row is the what-if's for that candidate** (RK-19), under pro-rata funding: its own
  listwise panel, the holdings plus the candidate, with the candidate at weight 0 for
  "before". A single shared panel over all candidates would drop every candidate's NULL weeks
  for all of them, and the rows would stop matching the what-if. Each figure carries its panel's
  `n_obs`, so a candidate's lost weeks show.
- **Only what ranking shows is measured**: the decomposition before and after (σ_p, the
  diversification ratio, the candidate's share of risk and correlation) and the two weekly
  Sharpes with Memmel's SE, on the same path `mix_report` takes, so a test pins each row to the
  what-if's. Max drawdown and the threshold stay in the what-if: the threshold explains one
  candidate, and ranking by it would be ranking by Sharpe.
- **The order is Δσ_p ascending or Δ diversification ratio descending**, ties by ticker.
  ΔSharpe is carried with its SE but never orders: the best of many noisy historical Sharpe
  deltas is mostly luck (§7.2). A row missing either key (its gaps put it under a minimum) is
  listed greyed after the ranking, so changing the order never moves a row between lists.
- **1Y is refused**: the diversification ratio needs 104 weeks, so every row would be greyed.
- **R14 doesn't apply**: the flow ranks tracked tickers only, so no figure is `survivorship`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from portfolio.risk.ex_ante import (
    ExAnteDecomposition,
    constant_weight_series,
    decomposition_figures,
)
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import weekly_figures, weekly_figures_without_rf
from portfolio.risk.types import ReturnSeries, Window
from portfolio.risk.weekly import to_weekly
from portfolio.risk.what_if import (
    PRO_RATA,
    Row,
    SharpeRow,
    delta,
    funded_weights,
    holding_figure,
    sharpe_delta_se,
)
from portfolio.risk.windows import (
    StandardWindow,
    common_span,
    earliest_common_start,
    latest_common_date,
    standard_windows,
)

SIGMA_P = "sigma_p"
DIVERSIFICATION_RATIO = "diversification_ratio"
RANK_KEYS = (SIGMA_P, DIVERSIFICATION_RATIO)


@dataclass(frozen=True)
class CandidateScore:
    ticker: str
    history_start: date  # the candidate's daily base
    n_weeks: int  # weekly returns over its whole history
    sigma_p: Row
    diversification_ratio: Row
    candidate_pcr: Row  # after only
    candidate_correlation: Row  # at weight 0: ρ(c, p) with the mix it would join
    sharpe: SharpeRow


@dataclass(frozen=True)
class Unranked:
    ticker: str
    history_start: date
    n_weeks: int
    reason: str  # R9's, in weeks inside the holdings' window


@dataclass(frozen=True)
class RankingReport:
    requested: Window  # the standard window asked for, ending on the holdings' latest date
    window: Window | None  # the holdings' effective one; None when they don't overlap
    weight: float
    rank_by: str
    ranked: tuple[CandidateScore, ...]  # in rank order
    greyed: tuple[CandidateScore, ...]  # scored, but a key is greyed; by ticker
    unranked: tuple[Unranked, ...]  # don't cover the window; by ticker


def rank_candidates(
    holdings: Mapping[str, ReturnSeries],
    weights: Mapping[str, float],
    candidates: Mapping[str, ReturnSeries],
    weight: float,
    label: StandardWindow,
    rank_by: str,
    *,
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
) -> RankingReport:
    """`holdings` and `candidates` are daily EUR returns, `weights` the holdings' normalised
    weights, `weight` each candidate's share of the mix after the change. `rates` and
    `first_published` are None together when there's no risk-free series."""
    if rank_by not in RANK_KEYS:
        raise ValueError(f"rank_by must be one of {', '.join(RANK_KEYS)}, got {rank_by!r}")
    if label is StandardWindow.Y1:
        raise ValueError("ranking can't use 1Y: the diversification ratio needs 104 weeks")
    if not holdings:
        raise ValueError("a ranking needs at least one holding")

    requested = _requested(holdings, label)
    weekly_holdings = {key: to_weekly(series) for key, series in holdings.items()}
    alone = decomposition_figures(weekly_holdings, weights, requested)
    window = None
    if common_span(*holdings.values()) is not None:
        start = max(alone.sigma_p.window_start, earliest_common_start(*holdings.values()))
        window = Window(label, start, requested.end)

    scored: list[CandidateScore] = []
    unranked: list[Unranked] = []
    for ticker in sorted(candidates):
        series = candidates[ticker]
        funded = funded_weights(weights, ticker, weight, PRO_RATA)
        daily = {**holdings, ticker: series}
        weekly = {**weekly_holdings, ticker: to_weekly(series)}
        zero = {**weights, ticker: 0.0}
        own = _requested(daily, label)  # the what-if's for this candidate
        before = decomposition_figures(weekly, zero, own)
        n_weeks = len(weekly[ticker].dates)
        if before.panel.window != alone.panel.window:
            unranked.append(
                Unranked(
                    ticker, series.base_date, n_weeks, _short(weekly[ticker], alone.panel.window)
                )
            )
            continue
        after = decomposition_figures(weekly, funded.after, own)
        scored.append(
            CandidateScore(
                ticker=ticker,
                history_start=series.base_date,
                n_weeks=n_weeks,
                sigma_p=_row(before.sigma_p, after.sigma_p),
                diversification_ratio=_row(
                    before.diversification_ratio, after.diversification_ratio
                ),
                candidate_pcr=Row(None, holding_figure(after, ticker, "pcr"), None),
                candidate_correlation=Row(
                    None, holding_figure(before, ticker, "corr_with_portfolio"), None
                ),
                sharpe=_sharpe_row(
                    daily, weekly, zero, funded.after, before, label, rates, first_published
                ),
            )
        )

    ranked = [
        row
        for row in scored
        if _key(row, SIGMA_P) is not None and _key(row, DIVERSIFICATION_RATIO) is not None
    ]
    greyed = [row for row in scored if row not in ranked]
    sign = 1 if rank_by == SIGMA_P else -1  # less volatility, or more diversification, first
    ranked.sort(key=lambda row: (sign * _key(row, rank_by), row.ticker))
    return RankingReport(
        requested, window, weight, rank_by, tuple(ranked), tuple(greyed), tuple(unranked)
    )


def _requested(daily: Mapping[str, ReturnSeries], label: StandardWindow) -> Window:
    """The standard window `mix_report` asks for over `daily`."""
    end = latest_common_date(*daily.values())
    oldest = min(series.base_date for series in daily.values())
    return next(w for w in standard_windows(end, oldest) if w.label == label)


def _row(before, after) -> Row:
    return Row(before, after, delta(before, after))


def _key(row: CandidateScore, rank_by: str) -> float | None:
    return getattr(row, rank_by).delta


def _short(weekly: ReturnSeries, window: Window) -> str:
    """R9's reason for a candidate that doesn't cover the holdings' (weekly) window: the weekly
    returns it has inside it against the window's weeks."""
    have = sum(
        1
        for day, value in zip(weekly.dates, weekly.values)
        if window.start < day <= window.end and value is not None
    )
    need = (window.end - window.start).days // 7
    return f"insufficient history (have {have}, need {need})"


def _sharpe_row(
    daily: Mapping[str, ReturnSeries],
    weekly: Mapping[str, ReturnSeries],
    zero: Mapping[str, float],
    after_weights: Mapping[str, float],
    before_decomposition: ExAnteDecomposition,
    label: StandardWindow,
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
) -> SharpeRow:
    """The two mixes' weekly Sharpes over the window `mix_report` gives them, and ΔSharpe's SE."""
    end = latest_common_date(*daily.values())
    start = max(before_decomposition.sigma_p.window_start, earliest_common_start(*daily.values()))
    window = Window(label, start, end)
    mix_before = constant_weight_series(weekly, zero)
    mix_after = constant_weight_series(weekly, after_weights)

    if rates is None:
        sharpe_before = weekly_figures_without_rf(mix_before, window, NO_RISK_FREE).sharpe
        sharpe_after = weekly_figures_without_rf(mix_after, window, NO_RISK_FREE).sharpe
    else:
        sharpe_before = weekly_figures(mix_before, window, rates, first_published).sharpe
        sharpe_after = weekly_figures(mix_after, window, rates, first_published).sharpe
    change = delta(sharpe_before, sharpe_after)
    se = None
    if rates is not None and change is not None:
        se = sharpe_delta_se(mix_before, mix_after, window, rates, first_published)
    return SharpeRow(sharpe_before, sharpe_after, change, se)
