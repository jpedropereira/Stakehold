"""GET /labs (LB-6): the user's current holdings backtested over 1M to Max. And (LB-7) any mix
the user edits from there, held in the query string (labs_mix.py has the contract).

The default mix takes two calls, in order: GET /holdings (labs_view.build_default_mix), then
portfolio's POST /labs/backtest for it. The second needs the first's answer, so they can't run
concurrently. Each fails on its own: a failed holdings call shows its notice and runs no
backtest, and a failed backtest still shows which holdings the mix left out. The ticker list for
the form's suggestions is fetched alongside the holdings, and fails on its own too.

An edited mix needs no holdings. It is checked against the ticker list first. A mix with errors
isn't sent: the table shows the mix it last showed (the form's last_* fields), if there is one.

The backtest returns the growth series for one period, `?period=` (LB-8), which the chart
draws: buy and hold re-bases each period on its own first day, so a longer series can't be
sliced client-side. 1Y by default, and in place of anything that isn't a period. The period
links keep the rest of the query string, so they work with whatever mix it holds.

The backtest also carries risk figures (RK-9): a grid of the mix's measures by window, and
the mix and its assets compared over one window, `?risk_window=` (1Y by default, and in place of
anything that isn't a window). Every window is in the one response, so a window link changes
only what is shown; it still reruns the page, as the period links do, so the page works without
JS. labs_risk_view.py words the figures.

`?benchmark=` (RK-14) is what the relative figures and the growth chart's benchmark line measure
against, VWCE.DE by default (benchmark_param.py has the rule). Its form, in the risk section,
re-sends the rest of the query as hidden inputs, so it changes nothing else; every other link
and form on the page keeps it, as they keep the period and the risk window.

On its own router, like portfolio.py, because routes.py is close to the repo's 64KB file limit;
it renders through routes.py's _render, so a cookie set earlier in the request still reaches
the response.
"""

import asyncio
import logging
from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from web.api.csrf import ensure_csrf_cookie
from web.api.deps import get_portfolio_client, get_settings, get_templates, require_user
from web.api.routes import _load_tickers, _redirect, _render
from web.auth_client.base import AuthUser
from web.benchmark_param import chosen_benchmark
from web.labs_mix import (
    CheckedMix,
    MixRow,
    canonical_query,
    check_mix,
    default_rows,
    parse_mix,
)
from web.labs_risk_view import (
    RELATIVE_MEASURES,
    RISK_MEASURES,
    build_comparison,
    build_drawdown_payload,
    build_mix_grid,
)
from web.labs_view import (
    MAX_BACKTEST_ASSETS,
    DefaultMix,
    build_default_mix,
    build_growth_payload,
    mix_left_out_runs,
)
from web.portfolio_client.base import (
    BACKTEST_PERIODS,
    BACKTEST_RISK_WINDOWS,
    Backtest,
    BacktestRisk,
    PortfolioClient,
)
from web.portfolio_client.errors import InvalidPortfolioRequest, PortfolioClientError
from web.settings import Settings

logger = logging.getLogger(__name__)

router = APIRouter()

_UNAVAILABLE = "Can't reach the portfolio service right now, so it can't be shown."
_DEFAULT_PERIOD = "1Y"
_DEFAULT_RISK_WINDOW = "1Y"


async def _load_mix(portfolio_client: PortfolioClient, user_id: str) -> DefaultMix | None:
    """The default mix, or None if the holdings couldn't be loaded."""
    try:
        holdings = await portfolio_client.list_holdings(user_id=user_id)
    except PortfolioClientError:
        logger.exception("labs: portfolio service unavailable listing holdings.")
        return None
    mix = build_default_mix(holdings.holdings)
    logger.info(
        "labs: %d holdings, %d in the mix, %d unpriced, %d worth nothing, %d over the %d cap.",
        len(holdings.holdings),
        len(mix.assets),
        len(mix.unpriced),
        len(mix.unweighted),
        len(mix.dropped),
        MAX_BACKTEST_ASSETS,
    )
    return mix


def _chosen_period(requested: str | None) -> str:
    """`requested` if it is a period, exactly as portfolio spells it; otherwise the default."""
    if requested is None or requested in BACKTEST_PERIODS:
        return requested or _DEFAULT_PERIOD
    logger.info("labs: unknown period %r, showing %s.", requested, _DEFAULT_PERIOD)
    return _DEFAULT_PERIOD


def _chosen_risk_window(requested: str | None) -> str:
    """`requested` if it is a risk window, exactly as portfolio spells it; otherwise 1Y."""
    if requested is None or requested in BACKTEST_RISK_WINDOWS:
        return requested or _DEFAULT_RISK_WINDOW
    logger.info("labs: unknown risk window %r, showing %s.", requested, _DEFAULT_RISK_WINDOW)
    return _DEFAULT_RISK_WINDOW


def _risk_window_links(request: Request) -> dict[str, str]:
    """Each risk window's URL: this page's query string with only `risk_window` changed, back
    at the risk section, since the page reloads."""
    kept = [(k, v) for k, v in request.query_params.multi_items() if k != "risk_window"]
    return {
        w: "/labs?" + urlencode(kept + [("risk_window", w)]) + "#labs-risk"
        for w in BACKTEST_RISK_WINDOWS
    }


def _benchmark_kept(request: Request) -> list[tuple[str, str]]:
    """The query the benchmark form re-sends as hidden inputs: everything but `benchmark`, which
    its field sends. Not `remove`, which the route answers with a redirect before rendering."""
    return [(k, v) for k, v in request.query_params.multi_items() if k != "benchmark"]


def _log_risk(risk: BacktestRisk) -> None:
    covered = sum(1 for w in risk.mix.windows.values() if w.covered)
    start = (
        "no common window"
        if risk.common_start is None
        else f"windows from {risk.common_start} (limited by {', '.join(risk.limited_by)})"
    )
    rf = (
        "risk-free rate available"
        if risk.risk_free.available
        else f"no risk-free rate ({risk.risk_free.reason})"
    )
    bench = risk.benchmark
    line = (
        "growth line"
        if bench.growth is not None
        else f"no growth line: {bench.growth_reason or 'not available'}"
    )
    logger.info(
        "labs: risk to %s, %s, %d of %d covered, %s, against %s (%s, %s).",
        risk.end_date,
        start,
        covered,
        len(BACKTEST_RISK_WINDOWS),
        rf,
        bench.ticker,
        "available" if bench.available else f"unavailable: {bench.reason}",
        line,
    )


def _period_links(request: Request) -> dict[str, str]:
    """Each period's URL: this page's query string with only `period` changed."""
    kept = [(k, v) for k, v in request.query_params.multi_items() if k != "period"]
    return {p: "/labs?" + urlencode(kept + [("period", p)]) for p in BACKTEST_PERIODS}


async def _run_backtest(
    portfolio_client: PortfolioClient,
    assets: tuple[tuple[str, Decimal], ...],
    period: str,
    benchmark: str,
) -> tuple[Backtest | None, str | None]:
    """The backtest and None, or None and the message to show in its place."""
    try:
        result = await portfolio_client.backtest(
            assets=list(assets), period=period, benchmark=benchmark
        )
    except InvalidPortfolioRequest as exc:
        # portfolio rejected the mix; its message is written to be shown.
        logger.warning("labs: portfolio rejected the mix: %s", exc)
        return None, str(exc)
    except PortfolioClientError:
        logger.exception("labs: portfolio service unavailable running the backtest.")
        return None, _UNAVAILABLE
    included = sum(1 for s in result.statuses if s.status == "included")
    logger.info(
        "labs: backtest to %s with %s growth, %d included, %d left out.",
        result.end_date,
        period,
        included,
        len(result.statuses) - included,
    )
    return result, None


def _checked_last(last: tuple[MixRow, ...]) -> tuple[tuple[MixRow, ...], CheckedMix | None]:
    """The last mix the table showed, if it is still one that can be sent. Its tickers were
    checked when it was shown, so only its shape is checked again: a hand-edited URL with a
    bad last mix just shows no table."""
    checked = check_mix(last, None)
    if not last or not checked.valid:
        return (), None
    return last, checked


@router.get("/labs")
async def labs(
    request: Request,
    response: Response,
    settings: Settings = Depends(get_settings),
    templates=Depends(get_templates),
    user: AuthUser = Depends(require_user),
    portfolio_client: PortfolioClient = Depends(get_portfolio_client),
    period: str | None = None,
    risk_window: str | None = None,
    benchmark: str | None = None,
):
    form = parse_mix(request.query_params.multi_items())
    if form is not None and form.remove is not None:
        # A remove button submits the whole form; answer with the mix it leaves, so a reload
        # doesn't remove the next row too.
        # The chart's period, if one was chosen, stays chosen.
        query = canonical_query(form.without_removed(), form.last)
        if period is not None:
            query += "&" + urlencode([("period", period)])
        if risk_window is not None:
            query += "&" + urlencode([("risk_window", risk_window)])
        if benchmark is not None:
            query += "&" + urlencode([("benchmark", benchmark)])
        return _redirect(request, response, f"/labs?{query}")

    # The header's logout and theme forms need the token, as on every other page.
    csrf_token = ensure_csrf_cookie(request, response, cookie_secure=settings.cookie_secure)

    period = _chosen_period(period)
    risk_window = _chosen_risk_window(risk_window)
    benchmark, benchmark_error = chosen_benchmark(benchmark)
    if benchmark_error:
        logger.info("labs: benchmark field over the length limit, measuring against %s.", benchmark)
    mix: DefaultMix | None = None
    holdings_error = None
    checked: CheckedMix | None = None
    shown: tuple[MixRow, ...] = ()  # the mix the table shows, for the form's last_* fields
    to_send: tuple[tuple[str, Decimal], ...] = ()
    if form is None:
        mix, (tickers, tickers_notice) = await asyncio.gather(
            _load_mix(portfolio_client, user.user_id), _load_tickers(portfolio_client)
        )
        if mix is None:
            holdings_error = _UNAVAILABLE
        else:
            shown, to_send = default_rows(mix), mix.assets
            checked = check_mix(shown, None)
            if not mix.assets:
                logger.info("labs: nothing to backtest, no holding has a market value.")
    else:
        tickers, tickers_notice = await _load_tickers(portfolio_client)
        checked = check_mix(form.rows, None if tickers_notice else tickers)
        logger.info(
            "labs: edited mix, %d rows, %d with errors%s.",
            len(checked.rows),
            sum(1 for row in checked.rows if row.errors),
            f", {checked.error}" if checked.error else "",
        )
        if checked.valid:
            # In the tracked spelling, so the table and the form agree.
            shown = tuple(MixRow(row.ticker, row.weight) for row in checked.rows)
            to_send = checked.assets
            if not to_send:
                logger.info("labs: nothing to backtest, the edited mix is empty.")
        else:
            shown, last = _checked_last(form.last)
            to_send = () if last is None else last.assets
            logger.info(
                "labs: %s",
                (
                    f"backtesting the last valid mix instead, {len(to_send)} assets."
                    if to_send
                    else "no last valid mix to backtest instead."
                ),
            )

    backtest, backtest_error = None, None
    if to_send:
        backtest, backtest_error = await _run_backtest(portfolio_client, to_send, period, benchmark)
    risk = None if backtest is None else backtest.risk
    if risk is not None:
        _log_risk(risk)
    return _render(
        request,
        response,
        templates,
        "labs.html",
        {
            "user": user,
            "csrf_token": csrf_token,
            "nav_active": "labs",
            "edited": form is not None,
            "holdings_error": holdings_error,
            "mix": mix,
            "max_assets": MAX_BACKTEST_ASSETS,
            "checked": checked,
            "shown": shown,
            "sent": bool(to_send),
            # The form has errors, so the table shows the mix it showed before.
            "showing_last": checked is not None and not checked.valid and bool(to_send),
            "tickers": tickers,
            "tickers_notice": tickers_notice,
            "backtest": backtest,
            "backtest_error": backtest_error,
            "periods": BACKTEST_PERIODS,
            "period": period,
            "period_links": _period_links(request),
            "growth": (
                None
                if backtest is None or backtest.growth is None
                else build_growth_payload(backtest.growth)
            ),
            "left_out_runs": (
                [] if backtest is None or backtest.mix is None else mix_left_out_runs(backtest.mix)
            ),
            "risk": risk,
            "risk_grid": None if risk is None else build_mix_grid(risk),
            "risk_comparison": None if risk is None else build_comparison(risk, risk_window),
            "risk_window": risk_window,
            "risk_measures": RISK_MEASURES,
            "relative_measures": RELATIVE_MEASURES,
            "benchmark": benchmark,
            "benchmark_error": benchmark_error,
            "benchmark_kept": _benchmark_kept(request),
            "risk_window_links": _risk_window_links(request),
            "drawdown": (
                None
                if risk is None or risk.drawdown is None
                else build_drawdown_payload(risk.drawdown)
            ),
            "unavailable": (
                [] if backtest is None else [s for s in backtest.statuses if s.status != "included"]
            ),
        },
    )
