"""The candidate what-if (RK-19, spec §7.2): today's mix before and after adding a candidate at a
weight, funded pro-rata or by replacing one holding. Pure, like the rest of the library:
risk/flows.py fetches the series and normalises the holdings' weights.

- **"Before" holds the candidate at weight 0.** Both mixes then run on one listwise panel
  (R12): the same window and the same weeks, including weeks lost to the candidate's NULLs, so
  before and after are comparable, as §7.2 requires. If the candidate starts later (or ends
  earlier) than the holdings, the window is shorter than the holdings' own: `shortened` says
  so, `without_candidate` is the window they would have had, and "before" is the holdings on
  the shortened one. `ex_ante.py` accepts a zero weight for exactly this.
- **Funding.** Pro-rata scales every holding by `(1 − w_c)`. Replacing X moves up to `w_c` of
  X's weight to the candidate: when `w_c` exceeds X's weight, the candidate gets X's weight
  and `capped` says so. "From cash" is deferred until cash is tracked.
- **The rows lead with risk** (§7.2): σ_p, the diversification ratio, the candidate's share of
  risk and its correlation with the portfolio, then ΔSharpe with its SE, then max drawdown.
  The candidate's correlation is its `corr_with_portfolio` in *before*'s decomposition, at
  weight 0: ρ(c, p) with the portfolio it would join. Both candidate rows have no "before".
  Each is a RiskFigure on the panel's metadata, greyed with the diversification ratio.
- **ΔSharpe's SE** is Memmel's, for two Sharpes on the same weeks (`sharpe_difference_se`).
  The two mixes share most of their holdings, so the SE of the difference is far below either
  Sharpe's own.
- **The threshold** SR_c > ρ(c, p)·SR_p is shown under pro-rata funding only: it is the sign of
  the mix's Sharpe derivative at zero weight, `(σ_c/σ_p)(SR_c − ρ·SR_p)`, when c is funded from
  the whole portfolio. Under "replace X" the condition involves X too. All three inputs are
  weekly, on the panel's weeks, from excess returns: SR_c is the candidate's Sharpe, SR_p is
  "before"'s, and ρ is computed from the two excess series rather than taken from the
  decomposition (raw returns), so the identity is exact. The two ρ differ only through the
  risk-free rate's tiny weekly variance. The threshold greys with its inputs: no risk-free
  series, or under the covariance minimum (104 weeks), with that figure's reason.
- **R14.** When the candidate comes from the index constituent universe rather than
  `tracked_tickers` (the caller decides), every *after* figure and the candidate's Sharpe carry
  `survivorship`; *before* figures never do.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import date

import numpy as np
from portfolio.risk.ex_ante import ExAnteDecomposition, constant_weight_series
from portfolio.risk.ex_ante_report import MixReport, mix_report
from portfolio.risk.ex_post import NO_RISK_FREE
from portfolio.risk.measures import sharpe_difference_se
from portfolio.risk.quality import WEEKLY_Q
from portfolio.risk.risk_free import RatePoint
from portfolio.risk.single_series import weekly_excess, weekly_figures
from portfolio.risk.types import Flag, ReturnSeries, RiskFigure, Window
from portfolio.risk.weekly import to_weekly
from portfolio.risk.windows import StandardWindow

PRO_RATA_RULE = "pro_rata"
REPLACE_RULE = "replace"
NOT_PRO_RATA = "shown under pro-rata funding only"
_NO_OVERLAP = "the candidate's and the holdings' histories don't overlap"


@dataclass(frozen=True)
class Funding:
    rule: str  # PRO_RATA_RULE or REPLACE_RULE
    ticker: str | None = None  # the replaced holding, under REPLACE_RULE

    @classmethod
    def replace(cls, ticker: str) -> "Funding":
        return cls(REPLACE_RULE, ticker)


PRO_RATA = Funding(PRO_RATA_RULE)


@dataclass(frozen=True)
class FundedWeights:
    after: dict[str, float]  # the holdings' and the candidate's, summing to 1
    candidate_weight: float  # the requested weight, or the replaced holding's when capped
    capped: bool  # replace only: the request exceeded the replaced holding's weight


@dataclass(frozen=True)
class Row:
    before: RiskFigure | None  # None for the candidate's own rows
    after: RiskFigure
    delta: float | None  # after − before; None when either is greyed, or there's no before


@dataclass(frozen=True)
class SharpeRow(Row):
    delta_se: float | None = None  # Memmel's SE of the delta, 1 s.e.


@dataclass(frozen=True)
class Threshold:
    applicable: bool  # False under replace funding
    reason: str | None  # why it isn't shown; None when it is
    candidate_sharpe: RiskFigure | None  # None when not applicable
    correlation: float | None  # ρ(c, p) of the weekly excess returns on the panel
    portfolio_sharpe: float | None  # SR_p, "before"'s weekly Sharpe
    threshold: float | None  # ρ(c, p) × SR_p
    improves: bool | None  # SR_c > threshold


@dataclass(frozen=True)
class WhatIfReport:
    candidate: str
    funding: Funding
    funded: FundedWeights
    before: MixReport
    after: MixReport
    without_candidate: Window | None  # the holdings' own window; None if they don't overlap
    shortened: bool  # the candidate changed the window
    sigma_p: Row
    diversification_ratio: Row
    candidate_pcr: Row
    candidate_correlation: Row
    sharpe: SharpeRow
    max_drawdown: Row
    threshold: Threshold


def funded_weights(
    before: Mapping[str, float], candidate: str, weight: float, funding: Funding
) -> FundedWeights:
    """The mix after adding `candidate` at `weight` (a fraction of the whole mix) to `before`
    (normalised weights), under `funding`."""
    if candidate in before:
        raise ValueError(f"the candidate {candidate} is already a holding")
    if not 0 < weight < 1:
        raise ValueError(f"the candidate's weight must be between 0 and 1, got {weight}")
    if funding.rule == PRO_RATA_RULE:
        after = {key: w * (1 - weight) for key, w in before.items()}
        return FundedWeights({**after, candidate: weight}, weight, False)
    if funding.rule != REPLACE_RULE:
        raise ValueError(f"unknown funding rule {funding.rule!r}")
    if funding.ticker not in before:
        raise ValueError(f"replace: {funding.ticker} is not a holding")
    moved = min(weight, before[funding.ticker])
    after = {**before, funding.ticker: before[funding.ticker] - moved, candidate: moved}
    return FundedWeights(after, moved, weight > before[funding.ticker])


def what_if_report(
    daily: Mapping[str, ReturnSeries],
    before_weights: Mapping[str, float],
    candidate: str,
    weight: float,
    funding: Funding,
    label: StandardWindow,
    *,
    trading_days: Mapping[str, AbstractSet[date]],
    currencies: Mapping[str, str],
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
    survivorship: bool,
) -> WhatIfReport:
    """`daily` is the holdings' and the candidate's daily EUR returns, `before_weights` the
    holdings' normalised weights. `rates` and `first_published` are None together when there's
    no risk-free series. `survivorship` is R14's flag for the candidate."""
    funded = funded_weights(before_weights, candidate, weight, funding)
    zero = {**before_weights, candidate: 0.0}
    measure = dict(
        trading_days=trading_days,
        currencies=currencies,
        rates=rates,
        first_published=first_published,
    )
    before = mix_report(daily, zero, label, **measure)
    after = mix_report(daily, funded.after, label, **measure)
    holdings = {key: series for key, series in daily.items() if key != candidate}
    without = mix_report(holdings, before_weights, label, **measure).window

    def flagged(figure: RiskFigure) -> RiskFigure:
        if not survivorship:
            return figure
        return dataclasses.replace(figure, flags=figure.flags | {Flag.SURVIVORSHIP})

    def row(b: RiskFigure, a: RiskFigure) -> Row:
        return Row(b, flagged(a), delta(b, a))

    weekly = {key: to_weekly(series) for key, series in daily.items()}
    sharpe_before = before.constant_weight.weekly.sharpe
    sharpe_after = after.constant_weight.weekly.sharpe
    delta_se = None
    if (
        after.window is not None
        and rates is not None
        and delta(sharpe_before, sharpe_after) is not None
    ):
        delta_se = sharpe_delta_se(
            before.mix_weekly, after.mix_weekly, after.window, rates, first_published
        )

    return WhatIfReport(
        candidate=candidate,
        funding=funding,
        funded=funded,
        before=before,
        after=after,
        without_candidate=without,
        shortened=after.window != without,
        sigma_p=row(before.decomposition.sigma_p, after.decomposition.sigma_p),
        diversification_ratio=row(
            before.decomposition.diversification_ratio, after.decomposition.diversification_ratio
        ),
        candidate_pcr=Row(
            None, flagged(holding_figure(after.decomposition, candidate, "pcr")), None
        ),
        candidate_correlation=Row(
            None,
            flagged(holding_figure(before.decomposition, candidate, "corr_with_portfolio")),
            None,
        ),
        sharpe=SharpeRow(
            sharpe_before,
            flagged(sharpe_after),
            delta(sharpe_before, sharpe_after),
            delta_se,
        ),
        max_drawdown=row(
            before.constant_weight.drawdown.max_drawdown,
            after.constant_weight.drawdown.max_drawdown,
        ),
        threshold=_threshold(
            weekly, zero, before, candidate, funding, rates, first_published, flagged
        ),
    )


def delta(before: RiskFigure, after: RiskFigure) -> float | None:
    if before.value is None or after.value is None:
        return None
    return after.value - before.value


def sharpe_delta_se(
    before_weekly: ReturnSeries,
    after_weekly: ReturnSeries,
    window: Window,
    rates: Sequence[RatePoint],
    first_published: date,
) -> float:
    """Memmel's SE of after's weekly Sharpe minus before's, both on their excess returns over
    `window`'s weeks. Ranking (RK-29) gives each candidate's ΔSharpe this SE too."""
    return sharpe_difference_se(
        weekly_excess(before_weekly, window, rates, first_published),
        weekly_excess(after_weekly, window, rates, first_published),
        WEEKLY_Q,
    )


def holding_figure(decomposition: ExAnteDecomposition, key: str, field: str) -> RiskFigure:
    """One holding's decomposition value as a RiskFigure on the panel's metadata: the
    diversification ratio's, which greys exactly when the holdings table does. Ranking (RK-29)
    shows the candidate's rows this way too."""
    ratio = decomposition.diversification_ratio
    holding = next(h for h in decomposition.holdings if h.key == key)
    value = getattr(holding, field)
    if value is None:
        return dataclasses.replace(ratio, value=None)
    return dataclasses.replace(ratio, value=value, reason=None)


def _threshold(
    weekly: Mapping[str, ReturnSeries],
    zero: Mapping[str, float],
    before: MixReport,
    candidate: str,
    funding: Funding,
    rates: Sequence[RatePoint] | None,
    first_published: date | None,
    flagged,
) -> Threshold:
    if funding.rule != PRO_RATA_RULE:
        return Threshold(False, NOT_PRO_RATA, None, None, None, None, None)
    if before.window is None:
        return Threshold(True, _NO_OVERLAP, None, None, None, None, None)

    window = before.window
    alone = {key: 0.0 for key in weekly} | {candidate: 1.0}
    candidate_mix = constant_weight_series(weekly, alone)
    if rates is None:
        return Threshold(True, NO_RISK_FREE, None, None, None, None, None)

    candidate_sharpe = flagged(weekly_figures(candidate_mix, window, rates, first_published).sharpe)
    portfolio_sharpe = before.constant_weight.weekly.sharpe
    ratio = before.decomposition.diversification_ratio
    for figure in (ratio, candidate_sharpe, portfolio_sharpe):
        if figure.value is None:
            return Threshold(True, figure.reason, candidate_sharpe, None, None, None, None)

    x_c = weekly_excess(candidate_mix, window, rates, first_published)
    x_p = weekly_excess(constant_weight_series(weekly, zero), window, rates, first_published)
    kept = ~np.isnan(x_c)
    rho = float(np.corrcoef(x_c[kept], x_p[kept])[0, 1])
    threshold = rho * portfolio_sharpe.value
    return Threshold(
        applicable=True,
        reason=None,
        candidate_sharpe=candidate_sharpe,
        correlation=rho,
        portfolio_sharpe=portfolio_sharpe.value,
        threshold=threshold,
        improves=candidate_sharpe.value > threshold,
    )
