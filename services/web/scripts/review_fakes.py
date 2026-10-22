"""The fake auth and portfolio clients review_pages.py serves its pages from, and the review data
they hold. Split out of review_pages.py (RK-23) to keep it under the repo's 64KB file limit; the
page list and the checks stay there.
"""

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal


def fakes():
    """Fake auth and portfolio clients serving the review data. Imported here rather than at
    module level, so the page list above can be read without the web package's dependencies."""
    from web.auth_client.base import AuthResult, AuthUser
    from web.auth_client.errors import SessionInvalid
    from web.portfolio_client.base import (
        BACKTEST_PERIODS,
        BACKTEST_RISK_WINDOWS,
        RELATIVE_FIGURE_KEYS,
        RISK_FIGURE_KEYS,
        RISK_WINDOWS,
        Account,
        Backtest,
        BacktestAssetStatus,
        BacktestBenchmark,
        BacktestGrowth,
        BacktestPeriodReturn,
        BacktestRisk,
        BacktestRow,
        BacktestRowRisk,
        BacktestWindowRisk,
        BenchmarkStatus,
        Broker,
        CorrelationCell,
        CorrelationMatrix,
        DrawdownSeries,
        ExAnteHolding,
        ExAnteRisk,
        ExAnteWindow,
        ExcludedHolding,
        ExPostRisk,
        ExPostWindow,
        Holding,
        HoldingAccount,
        Holdings,
        HoldingsTotals,
        LookThrough,
        LookThroughAsset,
        LookThroughBucket,
        LookThroughConcentration,
        LookThroughDimension,
        LookThroughExposures,
        LookThroughName,
        LookThroughNames,
        LookThroughSnapshot,
        PortfolioReturns,
        PortfolioSeriesPoint,
        Position,
        Return,
        RiskFigure,
        RiskFreeStatus,
        SeriesRisk,
        TickerLookup,
        TickerMapping,
        TrackedTicker,
        Transaction,
        UnmappedInstrument,
    )
    from web.portfolio_client.errors import PortfolioUnavailable
    from web.portfolio_view import aggregate_portfolio_returns

    # The benchmarks the review data has (RK-13): VWCE.DE's short history greys 10Y, IWDA.AS
    # covers every window, and any other ticker is unavailable, as portfolio reports one it
    # doesn't track.
    benchmark_firsts = {"VWCE.DE": date(2019, 7, 29), "IWDA.AS": date(2009, 9, 25)}

    def review_relative(benchmark, start, weeks):
        """A window's figures against `benchmark` (RK-13). Beta and correlation need 104
        weeks, so a 1Y window greys them; the other two need 52."""
        first = benchmark_firsts.get(benchmark)

        def figure(value, minimum=52, reason=None):
            if reason is None and weeks < minimum:
                reason = f"insufficient history (have {weeks}, need {minimum})"
            return RiskFigure(
                value=None if reason else value,
                window_start=start,
                window_end=today,
                frequency="weekly",
                q=52.0,
                n_obs=0 if first is None or first > start else weeks,
                n_gaps=0,
                flags=("insufficient_history",) if reason else (),
                reason=reason,
                standard_error=None,
            )

        reason = None
        if first is None:
            reason = "benchmark unavailable: not tracked"
        elif first > start:
            reason = f"benchmark history too short (from {first})"
        scale = 1.0 if benchmark == "VWCE.DE" else 1.04
        return {
            "beta": figure(0.94 * scale, 104, reason),
            "correlation": figure(0.91 / scale, 104, reason),
            "tracking_error": figure(0.0412 * scale, 52, reason),
            "information_ratio": figure(-0.18 * scale, 52, reason),
        }

    def review_benchmark(benchmark, days, mix_values):
        """The backtest's benchmark (RK-13): its growth line follows the mix's a little lower,
        with its own wobble, when its history covers the period."""
        first = benchmark_firsts.get(benchmark)
        if first is None:
            reason = "benchmark unavailable: not tracked"
            return BacktestBenchmark(benchmark, False, "not tracked", None, None, reason)
        if mix_values is None or first > days[0]:
            reason = None if mix_values is None else f"benchmark history too short (from {first})"
            return BacktestBenchmark(benchmark, True, None, first, None, reason)
        line = tuple(
            Decimal(str(round(0.92 * float(v) + 0.006 * math.sin(i / 7), 6)))
            for i, v in enumerate(mix_values)
        )
        return BacktestBenchmark(benchmark, True, None, first, line, None)

    def review_risk(assets, period, days, mix_values, benchmark):
        """The backtest's risk section (RK-8) for the review mix. AAPL's history starts on
        2021-11-01, so with it in the mix every window starts no earlier, and 5Y and 10Y aren't
        covered; without it, VWCE.DE limits the windows to 2019-07-29. The figures exercise
        every state the panel shows: a short-window note (1Y's CAGR and Calmar), a gap note
        (AAPL's 3Y volatility), a greyed ratio with its reason (AAPL's 1Y Sortino), and, for a
        mix with NEWCO.DE, no risk-free data at all. The drawdown follows the mix's growth line,
        with one gap date."""
        firsts = {"VWCE.DE": date(2019, 7, 29), "AAPL": date(2021, 11, 1)}
        tickers = [t for t, _ in assets]
        common = max(firsts.get(t, firsts["VWCE.DE"]) for t in tickers)
        limited_by = tuple(t for t in tickers if firsts.get(t, firsts["VWCE.DE"]) == common)
        no_rf = "NEWCO.DE" in tickers
        rf_reason = "no risk-free rate data"
        years = {"1Y": 1, "3Y": 3, "5Y": 5, "10Y": 10}
        # (cagr, volatility, max drawdown, sharpe, its SE, sortino) per row, before scaling.
        base = {
            None: (0.071, 0.154, -0.184, 0.86, 0.59, 1.23),
            "VWCE.DE": (0.068, 0.142, -0.171, 0.82, 0.58, 1.17),
            "AAPL": (0.094, 0.271, -0.309, 0.61, 0.57, 0.88),
        }

        def figure(value, frequency, n_obs, start, *, flags=(), reason=None, se=None):
            return RiskFigure(
                value=None if reason else value,
                window_start=start,
                window_end=today,
                frequency=frequency,
                q=252.0 if frequency == "daily" else 52.0,
                n_obs=n_obs,
                n_gaps=0,
                flags=tuple(sorted(flags)),
                reason=reason,
                standard_error=None if reason else se,
            )

        def window(ticker, name):
            start = common if name == "Max" else date(today.year - years[name], 10, 9)
            if start < common:
                reason = f"limited by {', '.join(limited_by)} (history from {common})"
                return BacktestWindowRisk(
                    None,
                    today,
                    False,
                    reason,
                    {k: None for k in RISK_FIGURE_KEYS},
                    None,
                    None,
                    None,
                    None,
                    None,
                )
            span = (today - start).days / 365.25
            daily, weekly = round(252 * span), int(52 * span)
            cagr, vol, mdd, sharpe, se, sortino = base.get(ticker, base["VWCE.DE"])
            scale = 1 + 0.05 * BACKTEST_RISK_WINDOWS.index(name)
            short = ("short_window",) if span < 3 else ()
            rf = {"reason": rf_reason} if no_rf else {}
            greyed = ticker == "AAPL" and name == "1Y" and not no_rf
            figures = {k: figure(0.01, "daily", daily, start) for k in RISK_FIGURE_KEYS}
            figures.update(
                cagr=figure(cagr * scale, "daily", daily, start, flags=short),
                volatility=figure(
                    vol * scale,
                    "weekly",
                    weekly,
                    start,
                    flags=("data_gaps",) if ticker == "AAPL" and name == "3Y" else (),
                ),
                max_drawdown=figure(mdd * scale, "daily", daily, start),
                sharpe=figure(sharpe / scale, "weekly", weekly, start, se=se / span**0.5, **rf),
                sortino=figure(
                    sortino / scale,
                    "weekly",
                    weekly,
                    start,
                    **({"reason": "no weekly return below the risk-free rate"} if greyed else rf),
                ),
                calmar=figure(cagr / abs(mdd), "daily", daily, start, flags=short),
            )
            return BacktestWindowRisk(
                start,
                today,
                True,
                None,
                figures,
                date(2026, 2, 2),
                date(2026, 4, 7),
                None,
                249,
                review_relative(benchmark, start, weekly),
            )

        def row(ticker):
            return BacktestRowRisk(ticker, {w: window(ticker, w) for w in BACKTEST_RISK_WINDOWS})

        drawdown = None
        if mix_values is not None:
            peak, values = 1.0, []
            for i, growth in enumerate(mix_values):
                level = 1 + float(growth)
                peak = max(peak, level)
                values.append(None if i == len(mix_values) // 3 else level / peak - 1)
            drawdown = DrawdownSeries(period, tuple(days), tuple(values))
        return BacktestRisk(
            label="buy & hold (drifting)",
            end_date=today,
            common_start=common,
            limited_by=limited_by,
            risk_free=(
                RiskFreeStatus(False, rf_reason, None)
                if no_rf
                else RiskFreeStatus(True, None, date(1999, 1, 4))
            ),
            benchmark=review_benchmark(benchmark, days, mix_values),
            mix=row(None),
            assets=tuple(row(t) for t in tickers),
            drawdown=drawdown,
        )

    users = {
        "admin-tok": AuthUser("u1", "ann@example.com", "Ann", is_admin=True),
        "user-tok": AuthUser("u2", "bo@example.com", "Bo"),
    }
    today = date(2026, 10, 9)

    class Auth:
        async def validate_session(self, *, cookie_header):
            for token, user in users.items():
                if f"stakehold_session={token}" in (cookie_header or ""):
                    return AuthResult(user=user, set_cookie=f"stakehold_session={token}; Path=/")
            raise SessionInvalid("no session")

    # (ticker, quantity, average cost, currency, market value or None while waiting for prices)
    holdings = [
        ("VWCE.DE", "42", "98.40", "EUR", "5124.84"),
        ("AAPL", "12.5", "182.10", "USD", "1978.40"),
        ("NEWCO.DE", "3", "20.00", "EUR", None),
    ]

    def latest(ticker, quantity, cost, value, on):
        cost_basis = Decimal(quantity) * Decimal(cost)
        unrealized = Decimal(value) - cost_basis
        dividends = Decimal("31.20") if ticker == "VWCE.DE" else Decimal("0")
        return Return(
            account_id=1,
            canonical_ticker=ticker,
            price_date=on,
            quantity=Decimal(quantity),
            market_value_native=Decimal(value),
            market_value_eur=Decimal(value),
            cost_basis_eur=cost_basis,
            unrealized_return_eur=unrealized,
            unrealized_return_pct=unrealized / cost_basis,
            dividends_received_eur=dividends,
            total_return_eur=unrealized + dividends,
            total_return_pct=(unrealized + dividends) / cost_basis,
        )

    def history():
        """A year of daily rows for the priced holdings, wandering around today's value."""
        rows = []
        for ticker, quantity, cost, _, value in holdings:
            if value is None:
                continue
            for back in range(365, -1, -1):
                drift = Decimal(str(round(1 + 0.08 * math.sin(back / 40) - back / 4000, 4)))
                on = today - timedelta(days=back)
                rows.append(latest(ticker, quantity, cost, Decimal(value) * drift, on))
        return rows

    def tracked(ticker, name, status, *, origin="config", requested_by=None, error=None):
        requested = datetime(2026, 9, 1, tzinfo=timezone.utc)
        return TrackedTicker(
            ticker=ticker,
            display_name=name,
            asset_type="ETF" if ticker.endswith(".DE") else "EQUITY",
            currency="USD" if ticker == "AAPL" else "EUR",
            exchange="NMS" if ticker == "AAPL" else "GER",
            origin=origin,
            status=status,
            enabled=True,
            attempts=3 if status == "failed" else 1,
            last_error=error,
            requested_by=requested_by,
            requested_at=requested,
            ready_at=requested if status == "ready" else None,
        )

    # Every status a ticker can show, the last two added by the plain user ("u2").
    tracked_tickers = [
        tracked("VWCE.DE", "Vanguard FTSE All-World", "ready"),
        tracked("AAPL", "Apple Inc.", "ready"),
        tracked("NEWCO.DE", "Newco AG", "backfilling", origin="request", requested_by="u2"),
        tracked(
            "OLDCO.DE",
            "Oldco AG",
            "failed",
            origin="request",
            requested_by="u2",
            error="no price history returned",
        ),
    ]

    def txn(ref, kind, day, amount, *, identifier=None, quantity=None, price=None, currency="EUR"):
        native = Decimal(amount)
        return Transaction(
            account_id=1,
            broker_transaction_ref=ref,
            transaction_type=kind,
            trade_datetime=datetime(2026, 9, day, 9, 30, tzinfo=timezone.utc),
            instrument_identifier=identifier,
            instrument_identifier_kind="TICKER" if identifier else None,
            quantity=None if quantity is None else Decimal(quantity),
            price=None if price is None else Decimal(price),
            native_currency=currency,
            native_amount=native,
            eur_amount=native * (Decimal("0.92") if currency == "USD" else 1),
            fx_rate=Decimal("0.92") if currency == "USD" else None,
            fx_rate_source="ECB" if currency == "USD" else None,
            fees_native=Decimal("1.00") if quantity else None,
        )

    class Portfolio:
        async def list_accounts(self, *, user_id):
            return [
                Account(1, "main", "acme", "2026-01-01T00:00:00Z"),
                Account(2, "pension", "globex", "2026-02-01T00:00:00Z"),
            ]

        async def list_brokers(self):
            return [
                Broker("acme", ("transactions", "cashflow")),
                Broker("globex", ("transactions",)),
            ]

        async def list_tickers(self):
            return ["AAPL", "VWCE.DE", "NEWCO.DE"]

        async def list_positions(self, *, user_id, account_label):
            return [
                Position(
                    1,
                    ticker,
                    today,
                    Decimal(qty),
                    Decimal(cost),
                    currency,
                    Decimal(cost),
                    Decimal(qty) * Decimal(cost),
                )
                for ticker, qty, cost, currency, _ in holdings
            ]

        async def list_latest_returns(self, *, user_id, account_label):
            return [
                latest(ticker, qty, cost, value, today)
                for ticker, qty, cost, _, value in holdings
                if value is not None
            ]

        async def list_returns(self, *, user_id, account_label):
            return history()

        async def list_portfolio_returns(self, *, user_id):
            """The per-account page's year of history, as portfolio's one series across
            accounts."""
            series = tuple(
                PortfolioSeriesPoint(
                    p.price_date,
                    p.market_value_eur,
                    p.cost_basis_eur,
                    p.dividends_received_eur,
                    p.total_return_eur,
                    p.total_return_pct,
                )
                for p in aggregate_portfolio_returns(history())
            )
            return PortfolioReturns(series=series)

        async def list_transactions(self, *, user_id, account_label):
            return [
                txn(
                    "c0a8f5e2-9b1d-4e7a-8f3c-2d6b9e1a7f40",
                    "BUY",
                    28,
                    "-2276.25",
                    identifier="AAPL",
                    quantity="12.5",
                    price="182.1000",
                    currency="USD",
                ),
                txn("div-0917", "DIVIDEND", 17, "31.20", identifier="VWCE.DE"),
                txn(
                    "buy-0904",
                    "BUY",
                    4,
                    "-4133.80",
                    identifier="VWCE.DE",
                    quantity="42",
                    price="98.4000",
                ),
                txn("dep-0901", "DEPOSIT", 1, "10000.00"),
            ]

        async def list_holdings(self, *, user_id):
            """The review holdings summed across accounts: VWCE.DE held in both accounts, AAPL
            in two currencies (so its native cells read "Mixed currencies"), and NEWCO.DE still
            waiting for prices."""

            def part(label, ticker, qty, cost, currency, value):
                cost_eur = Decimal(qty) * Decimal(cost)
                value_eur = None if value is None else Decimal(value)
                return HoldingAccount(
                    label,
                    Decimal(qty),
                    currency,
                    Decimal(cost),
                    cost_eur,
                    value_eur,
                    value_eur,
                    None if value_eur is None else value_eur - cost_eur,
                )

            def holding(ticker, parts, currency, dividends="0"):
                priced = all(p.market_value_eur is not None for p in parts)
                quantity = sum((p.quantity for p in parts), Decimal("0"))
                cost = sum((p.cost_basis_eur for p in parts), Decimal("0"))
                value = sum((p.market_value_eur or 0 for p in parts), Decimal("0"))
                unrealized = value - cost
                total = unrealized + Decimal(dividends)
                return Holding(
                    canonical_ticker=ticker,
                    quantity=quantity,
                    average_cost_eur=cost / quantity,
                    cost_basis_eur=cost,
                    native_currency=currency,
                    mixed_currency=currency is None,
                    average_cost_native=None if currency is None else cost / quantity,
                    market_value_native=None if currency is None or not priced else value,
                    priced=priced,
                    price_date=today if priced else None,
                    market_value_eur=value if priced else None,
                    unrealized_return_eur=unrealized if priced else None,
                    unrealized_return_pct=unrealized / cost if priced else None,
                    dividends_received_eur=Decimal(dividends) if priced else None,
                    total_return_eur=total if priced else None,
                    total_return_pct=total / cost if priced else None,
                    accounts=tuple(parts),
                )

            rows = (
                holding(
                    "AAPL",
                    [
                        part("main", "AAPL", "12.5", "182.10", "USD", "1978.40"),
                        part("pension", "AAPL", "4", "150.00", "GBP", "633.10"),
                    ],
                    None,
                ),
                holding("NEWCO.DE", [part("main", "NEWCO.DE", "3", "20.00", "EUR", None)], "EUR"),
                holding(
                    "VWCE.DE",
                    [
                        part("main", "VWCE.DE", "42", "98.40", "EUR", "5124.84"),
                        part("pension", "VWCE.DE", "10", "120.00", "EUR", "1220.20"),
                    ],
                    "EUR",
                    dividends="31.20",
                ),
            )
            priced = [r for r in rows if r.priced]
            cost = sum((r.cost_basis_eur for r in priced), Decimal("0"))
            total = sum((r.total_return_eur for r in priced), Decimal("0"))
            return Holdings(
                holdings=rows,
                totals=HoldingsTotals(
                    price_date=today,
                    market_value_eur=sum((r.market_value_eur for r in priced), Decimal("0")),
                    cost_basis_eur=cost,
                    dividends_received_eur=Decimal("31.20"),
                    total_return_eur=total,
                    total_return_pct=total / cost,
                ),
            )

        async def backtest(self, *, assets, period, benchmark):
            """The review holdings' mix (VWCE.DE and AAPL; NEWCO.DE has no price, so the page
            leaves it out). AAPL's history starts in November 2021: too short for 5Y, so the
            mix's 5Y figure and line are VWCE.DE's alone and name AAPL as left out, and its Max
            starts after the mix's, which joins it then (LB-9). The two Max lines end close
            together, so their labels are pushed apart. 7203.T was never requested: it is there
            so the review shows the list of assets portfolio couldn't backtest. The growth lines
            are smooth curves ending at the period's figure, one point per weekday, and the mix
            is their buy and hold."""
            returns = {
                None: ("0.0123", "0.0410", "-0.0215", "0.1180", "0.2402", "0.4120", "0.4950"),
                "VWCE.DE": ("0.0141", "0.0388", "-0.0102", "0.1305", "0.2650", "0.4120", "0.4380"),
                "AAPL": ("0.0087", "0.0452", "-0.0440", "0.0930", "0.1910", None, "0.4310"),
            }
            starts = (
                date(2026, 9, 9),
                date(2026, 7, 9),
                date(2026, 4, 9),
                date(2025, 10, 9),
                date(2024, 10, 9),
                date(2021, 10, 9),
                date(2019, 7, 25),  # Max: VWCE.DE's first day, so the mix's
            )
            # Each asset's own Max start, where it is later than the mix's.
            max_starts = {"AAPL": date(2021, 11, 1)}
            column = BACKTEST_PERIODS.index(period)

            def figures_of(ticker):
                # An edited mix can name a ticker the review data has no figures for.
                return returns.get(ticker, returns["VWCE.DE"])

            def left_out_of(p):
                """The assets with no figure for period `p`, which the mix's leaves out."""
                return tuple(
                    t for t, _ in assets if figures_of(t)[BACKTEST_PERIODS.index(p)] is None
                )

            covered = [(t, w) for t, w in assets if t not in left_out_of(period)]

            def row(ticker, weight):
                figures = figures_of(ticker)
                periods = {
                    p: BacktestPeriodReturn(
                        start=max_starts.get(ticker, start) if p == "Max" else start,
                        end=today,
                        covered=figure is not None,
                        return_pct=None if figure is None else Decimal(figure),
                        gaps=0,
                        left_out=left_out_of(p) if ticker is None else (),
                    )
                    for p, start, figure in zip(BACKTEST_PERIODS, starts, figures, strict=True)
                }
                return BacktestRow(ticker=ticker, weight=weight, periods=periods)

            total = sum((w for _, w in assets), Decimal("0"))
            days = [
                starts[column] + timedelta(days=n)
                for n in range((today - starts[column]).days + 1)
                if (starts[column] + timedelta(days=n)).weekday() < 5
            ]

            def line(ticker, wobble):
                figure = figures_of(ticker)[column]
                if figure is None:
                    return None
                # A late Max line is null before its own first day, then 0 on it.
                first = 0
                if period == "Max" and ticker in max_starts:
                    first = next(i for i, d in enumerate(days) if d >= max_starts[ticker])
                last = len(days) - 1 - first
                return tuple(
                    (
                        None
                        if i < first
                        else Decimal(
                            str(
                                round(
                                    float(figure) * (i - first) / last
                                    + wobble * math.sin((i - first) / 9),
                                    6,
                                )
                            )
                        )
                    )
                    for i in range(len(days))
                )

            lines = {t: line(t, 0.004 * (k + 1) * (-1) ** k) for k, (t, _) in enumerate(assets)}

            def mix_line():
                """Buy and hold over the covered assets, split again by their weights each
                time a late one joins."""
                if not covered:
                    return None
                values, base, segment, weights = [], Decimal("1"), 0, {}
                for i in range(len(days)):
                    present = [(t, w) for t, w in covered if lines[t][i] is not None]
                    if {t for t, _ in present} != set(weights):
                        base = base if i == 0 else 1 + values[-1]
                        segment = i
                        held = sum((w for _, w in present), Decimal("0"))
                        weights = {t: w / held for t, w in present}
                    growth = sum(
                        w * (1 + lines[t][i]) / (1 + lines[t][segment]) for t, w in weights.items()
                    )
                    values.append(base * growth - 1)
                return tuple(values)

            mix_values = mix_line()
            return Backtest(
                end_date=today,
                mix=row(None, Decimal("1")),
                assets=tuple(row(t, w / total) for t, w in assets),
                growth=BacktestGrowth(period, tuple(days), mix_values, lines, left_out_of(period)),
                statuses=tuple(BacktestAssetStatus(t, "included", None) for t, _ in assets)
                + (
                    BacktestAssetStatus(
                        "7203.T", "no_fx_rate", "Stakehold has no JPY exchange rates."
                    ),
                ),
                risk=review_risk(assets, period, days, mix_values, benchmark),
            )

        async def ex_post_risk(self, *, user_id, benchmark):
            """Three and a half years of history: 1Y, 3Y and Max are measured, 5Y and 10Y are
            greyed for starting before it. Every state the table has: a loss and a gain, a
            Sharpe with its standard error, a 1Y Sharpe greyed for too few weeks, VaR flagged
            for mixing markets and the 99% tail flagged as unstable, a recovered and an
            unrecovered drawdown, and a holding left out for having no FX series."""
            base = date(2023, 4, 3)
            weekly = {"volatility", "downside_deviation", "sharpe", "sortino"}
            measures = (
                "cumulative_return",
                "cagr",
                "volatility",
                "downside_deviation",
                "max_drawdown",
                "sharpe",
                "sortino",
                "calmar",
            )
            values = {
                "1Y": (-0.0412, -0.0412, 0.1534, 0.1121, -0.1873, -0.52, -0.68, -0.22),
                "3Y": (0.2215, 0.0690, 0.1402, 0.0987, -0.1873, 0.31, 0.44, 0.37),
                "Max": (0.3120, 0.0812, 0.1388, 0.0954, -0.1873, 0.42, 0.61, 0.43),
            }
            tails = {"var_95": 0.0189, "cvar_95": 0.0271, "var_99": 0.0342, "cvar_99": 0.0418}
            starts = {
                "1Y": date(2025, 10, 9),
                "3Y": date(2023, 10, 9),
                "5Y": date(2021, 10, 9),
                "10Y": date(2016, 10, 9),
                "Max": base,
            }

            def figure(name, start, value, **changes):
                fields = dict(
                    value=value,
                    window_start=start,
                    window_end=today,
                    frequency="weekly" if name in weekly else "daily",
                    q=52.0 if name in weekly else 251.6,
                    n_obs=round((today - start).days / (7 if name in weekly else 1.45)),
                    n_gaps=0,
                    flags=(),
                    reason=None,
                    standard_error=None,
                )
                fields.update(changes)
                return RiskFigure(**fields)

            def window(label):
                start = starts[label]
                if label not in values:
                    reason = f"insufficient history (portfolio history from {base})"
                    return ExPostWindow(
                        label,
                        start,
                        today,
                        False,
                        {
                            n: figure(
                                n, start, None, flags=("insufficient_history",), reason=reason
                            )
                            for n in RISK_FIGURE_KEYS
                        },
                        None,
                        None,
                        None,
                        None,
                        # Greyed like the rest: the portfolio's history doesn't cover it.
                        {
                            n: figure(
                                n,
                                start,
                                None,
                                flags=("insufficient_history",),
                                reason=reason,
                                frequency="weekly",
                                q=52.0,
                            )
                            for n in RELATIVE_FIGURE_KEYS
                        },
                    )
                years = (today - start).days / 365.25
                figures = {**dict(zip(measures, values[label])), **tails}
                result = {}
                for name, value in figures.items():
                    flags = ["short_window"] if name in ("cagr", "calmar") and years < 3 else []
                    if name.startswith(("var", "cvar")):
                        flags.append("nonsync_daily")
                    if name.endswith("_99") and years < 4:
                        flags.append("tail_unstable")
                    se = round(0.95 / math.sqrt(years), 2) if name == "sharpe" else None
                    result[name] = figure(name, start, value, flags=tuple(flags), standard_error=se)
                if label == "1Y":
                    result["sharpe"] = figure(
                        "sharpe",
                        date(2025, 10, 10),
                        None,
                        n_obs=50,
                        flags=("insufficient_history",),
                        reason="insufficient history (have 50, need 52)",
                    )
                recovered = None if label == "1Y" else date(2024, 11, 20)
                return ExPostWindow(
                    label,
                    start,
                    today,
                    True,
                    result,
                    date(2024, 7, 16) if label != "1Y" else date(2026, 1, 5),
                    date(2024, 8, 5) if label != "1Y" else date(2026, 4, 8),
                    recovered,
                    127 if recovered else 277,
                    review_relative(benchmark, start, round((today - start).days / 7)),
                )

            return ExPostRisk(
                label="ex-post · invested assets",
                base_date=base,
                end_date=today,
                rf_available=True,
                benchmark=BenchmarkStatus(
                    benchmark,
                    benchmark in benchmark_firsts,
                    None if benchmark in benchmark_firsts else "not tracked",
                    benchmark_firsts.get(benchmark),
                ),
                windows=tuple(window(label) for label in RISK_WINDOWS),
                excluded=(ExcludedHolding("7203.T", "no_fx_rate", "no FX series for JPY"),),
            )

        async def ex_ante_risk(self, *, assets, window, benchmark, exclude):
            """The review mix at constant weights (RK-18). AAPL's history starts in 2015, so it
            limits Max; 1Y has too few weeks for the decomposition and the squares; 10Y fails."""
            if window == "10Y":
                raise PortfolioUnavailable("review: 10Y fails on purpose")
            years = {"1Y": 1, "3Y": 3, "5Y": 5, "Max": 17}[window]
            requested = date(today.year - years, today.month, today.day)
            tickers = [t for t, _ in assets if t not in exclude]
            limited = "AAPL" in tickers and requested < date(2019, 7, 29)
            start = max(requested, date(2019, 7, 29) if "VWCE.DE" in tickers else requested)
            start = max(start, date(2015, 3, 6)) if "AAPL" in tickers else start
            weeks = (today - start).days // 7
            short = weeks < 104

            def figure(value, daily=False, reason=None):
                return RiskFigure(
                    None if reason else value,
                    start,
                    today,
                    "daily" if daily else "weekly",
                    251.6 if daily else 52.0,
                    weeks * (5 if daily else 1),
                    0,
                    ("insufficient_history",) if reason else (),
                    reason,
                    None,
                )

            grey = f"insufficient history (have {weeks}, need 104)" if short else None
            total = sum(w for t, w in assets if t in tickers)
            weights = {t: float(w / total) for t, w in assets if t in tickers}
            pcrs = {"VWCE.DE": 0.54, "AAPL": 0.46} if len(tickers) == 2 else {tickers[0]: 1.0}
            daily = {"cumulative_return", "cagr", "max_drawdown", "calmar"}
            values = (
                0.31,
                0.081,
                0.152,
                0.101,
                0.48,
                0.66,
                0.43,
                -0.212,
                0.0167,
                0.0244,
                0.0298,
                0.0371,
            )
            figures = {
                n: figure(v, daily=n in daily or n.startswith(("var", "cvar")))
                for n, v in zip(RISK_FIGURE_KEYS, values)
            }
            keys = tickers + ([] if benchmark in tickers else [benchmark])
            rho = {
                ("VWCE.DE", "AAPL"): 0.71,
                ("VWCE.DE", "IWDA.AS"): 0.98,
                ("AAPL", "IWDA.AS"): 0.74,
            }
            return ExAnteRisk(
                label="ex-ante · constant weights",
                currency="eur",
                rf_available=True,
                window=ExAnteWindow(window, requested, start, today, ("AAPL",) if limited else ()),
                benchmark=BenchmarkStatus(benchmark, True, None, benchmark_firsts.get(benchmark)),
                statuses=tuple(
                    (
                        BacktestAssetStatus(t, "excluded", "excluded by request")
                        if t in exclude
                        else BacktestAssetStatus(t, "included", None)
                    )
                    for t, _ in assets
                ),
                sigma_p=figure(0.152),
                diversification_ratio=figure(1.18, reason=grey),
                effective_n=1 / sum(w * w for w in weights.values()),
                holdings=tuple(
                    ExAnteHolding(
                        t,
                        w,
                        *(
                            (None,) * 5
                            if short
                            else (
                                0.17 + pcrs[t] / 5,
                                0.15,
                                pcrs[t] * 0.152,
                                pcrs[t],
                                0.9 - pcrs[t] / 4,
                            )
                        ),
                    )
                    for t, w in weights.items()
                ),
                constant_weight=SeriesRisk(
                    figures, date(2022, 1, 4), date(2022, 9, 30), date(2023, 12, 14), 709
                ),
                correlation=CorrelationMatrix(
                    keys=tuple(keys),
                    benchmark_key=benchmark,
                    cells=tuple(
                        CorrelationCell(
                            a,
                            b,
                            figure(
                                1.0 if a == b else rho.get((a, b), rho.get((b, a))), reason=grey
                            ),
                        )
                        for i, a in enumerate(keys)
                        for b in keys[i:]
                    ),
                ),
            )

        async def look_through(self, *, assets):
            """The concentration section (RK-23), shaped like the dev run (RK-22), not the
            review holdings: they hold no fund portfolio can look through. 70.7% through
            IWDA.AS, gold as a commodity, three unmapped funds (the prominent note), a direct
            stock merged with its IWDA line and one portfolio can't place, and more than 10
            countries and names, so each table has a "show all"."""
            d = Decimal

            def concentration(top1, known, hhi, reason=None):
                return LookThroughConcentration(
                    top_shares={1: d(top1), 5: d(known) * d("0.8"), 10: d(known) * d("0.9")},
                    known_share=d(known),
                    herfindahl=None if reason else d(hhi),
                    effective_n=None if reason else 1 / d(hhi),
                    reason=reason,
                )

            unmapped = (
                ("unmapped: VWCE.DE", "0.080", "unmapped"),
                ("unmapped: IEMA.AS", "0.030", "unmapped"),
                ("unmapped: IUSN.DE", "0.018", "unmapped"),
            )

            def dimension(label, known, *buckets, top1="0.5", hhi="0.3", looked=True):
                # The trading-currency view isn't looked through: an unmapped fund's own
                # trading currency is known, so it has no unmapped bucket.
                rows = (*buckets, *unmapped) if looked else buckets
                return LookThroughDimension(
                    label,
                    tuple(LookThroughBucket(b, d(w), k) for b, w, k in rows),
                    concentration(top1, known, hhi),
                )

            countries = (
                ("United States", "0.522"), ("Japan", "0.039"), ("United Kingdom", "0.025"),
                ("Canada", "0.021"), ("France", "0.021"), ("Switzerland", "0.018"),
                ("Germany", "0.017"), ("Australia", "0.011"), ("Netherlands", "0.010"),
                ("Denmark", "0.006"), ("Sweden", "0.006"), ("Italy", "0.005"),
                ("Spain", "0.005"), ("Hong Kong", "0.004"), ("Singapore", "0.003"),
                ("Finland", "0.002"), ("Belgium", "0.002"), ("Israel", "0.002"),
            )  # fmt: skip
            names = (
                ("Commodity: gold", None, "0.131", ("EGLN.L",)),
                ("NVIDIA CORP", "United States", "0.038", ("IWDA.AS",)),
                ("APPLE INC", "United States", "0.035", ("IWDA.AS",)),
                ("MICROSOFT CORP", "United States", "0.031", ("IWDA.AS",)),
                ("ALPHABET INC", "United States", "0.022", ("IWDA.AS",)),
                ("ALIBABA GROUP HOLDING LTD", None, "0.021", ("BABA",)),
                ("AMAZON COM INC", "United States", "0.020", ("IWDA.AS",)),
                ("BERKSHIRE HATHAWAY INC", "United States", "0.016", ("IWDA.AS", "BRK-B")),
                ("META PLATFORMS INC", "United States", "0.014", ("IWDA.AS",)),
                ("BROADCOM INC", "United States", "0.012", ("IWDA.AS",)),
                ("TESLA INC", "United States", "0.009", ("IWDA.AS",)),
                ("JPMORGAN CHASE & CO", "United States", "0.008", ("IWDA.AS",)),
                ("ELI LILLY", "United States", "0.007", ("IWDA.AS",)),
                ("VISA INC", "United States", "0.006", ("IWDA.AS",)),
                ("ASML HOLDING NV", "Netherlands", "0.005", ("IWDA.AS",)),
            )
            return LookThrough(
                assets=(
                    LookThroughAsset("IWDA.AS", "included", None, "look_through", d("0.707"),
                                     None, None),
                    LookThroughAsset("EGLN.L", "included", None, "commodity", d("0.131"),
                                     None, None),
                    LookThroughAsset("VWCE.DE", "included", None, "unmapped", d("0.080"),
                                     None, "no holdings data for this ETF"),
                    LookThroughAsset("IEMA.AS", "included", None, "unmapped", d("0.030"),
                                     None, "no holdings data for this ETF"),
                    LookThroughAsset("IUSN.DE", "included", None, "unmapped", d("0.018"),
                                     None, "no holdings snapshot"),
                    LookThroughAsset("BABA", "included", None, "direct", d("0.021"),
                                     None, None),
                    LookThroughAsset("BRK-B", "included", None, "direct", d("0.013"),
                                     "IWDA.AS", None),
                ),  # fmt: skip
                exposures=LookThroughExposures(
                    snapshots=(
                        LookThroughSnapshot("IWDA.AS", "world", date(2026, 8, 30), 1324,
                                            d("100.03")),
                    ),  # fmt: skip
                    coverage={
                        "look_through": d("0.707"),
                        "direct_matched": d("0.013"),
                        "direct_unmatched": d("0.021"),
                        "commodity": d("0.131"),
                        "unmapped": d("0.128"),
                    },
                    dimensions={
                        "currency": dimension(
                            "listing currency of holdings",
                            "0.872",
                            ("USD", "0.674", "known"),
                            ("EUR", "0.057", "known"),
                            ("JPY", "0.039", "known"),
                            ("GBP", "0.025", "known"),
                            ("CAD", "0.021", "known"),
                            ("CHF", "0.018", "known"),
                            ("AUD", "0.011", "known"),
                            ("DKK", "0.007", "known"),
                            ("SEK", "0.006", "known"),
                            ("HKD", "0.004", "known"),
                            ("SGD", "0.003", "known"),
                            ("NOK", "0.002", "known"),
                            top1="0.674",
                            hhi="0.61",
                        ),
                        "trading_currency": dimension(
                            "trading currency of positions",
                            "1",
                            ("EUR", "0.835", "known"),
                            ("GBP", "0.131", "known"),
                            ("USD", "0.034", "known"),
                            top1="0.835",
                            hhi="0.715",
                            looked=False,
                        ),
                        "sector": dimension(
                            "sector",
                            "0.851",
                            ("Information Technology", "0.180", "known"),
                            ("Financials", "0.133", "known"),
                            ("Commodity: gold", "0.131", "other"),
                            ("Industrials", "0.075", "known"),
                            ("Health Care", "0.070", "known"),
                            ("Consumer Discretionary", "0.070", "known"),
                            ("Communication", "0.050", "known"),
                            ("Consumer Staples", "0.040", "known"),
                            ("Energy", "0.030", "known"),
                            ("Materials", "0.025", "known"),
                            ("Utilities", "0.020", "known"),
                            ("Real Estate", "0.015", "known"),
                            ("Cash and/or Derivatives", "0.002", "known"),
                            ("unknown", "0.021", "unknown"),
                            top1="0.18",
                            hhi="0.14",
                        ),
                        "country": dimension(
                            "country",
                            "0.72",
                            *((c, w, "known") for c, w in countries),
                            ("n/a (commodity)", "0.131", "other"),
                            ("unknown", "0.021", "unknown"),
                            top1="0.522",
                            hhi="0.53",
                        ),
                        "asset_class": dimension(
                            "asset class",
                            "0.872",
                            ("Equity", "0.739", "known"),
                            ("Commodity", "0.131", "known"),
                            ("Cash", "0.002", "known"),
                            top1="0.739",
                            hhi="0.74",
                        ),
                    },
                    names=LookThroughNames(
                        top=tuple(
                            LookThroughName(issuer, location, d(w), sources)
                            for issuer, location, w, sources in names
                        ),
                        n_names=1311,
                        concentration=concentration("0.131", "0.872", "0.031"),
                    ),
                ),
            )

        async def list_unmapped_instruments(self, *, user_id):
            return [UnmappedInstrument("ISIN", "IE00B4L5Y983", None)]

        async def list_all_unmapped_instruments(self):
            return [
                UnmappedInstrument("ISIN", "IE00B4L5Y983", None),
                UnmappedInstrument("SYMBOL", "SXR8", "acme"),
            ]

        async def list_ticker_mappings(self):
            updated = datetime(2026, 9, 2, tzinfo=timezone.utc)
            return [
                TickerMapping(1, "ISIN", "IE00BK5BQT80", None, "VWCE.DE", updated),
                TickerMapping(2, "SYMBOL", "APC", "acme", "AAPL", updated),
            ]

        async def list_tracked_tickers(self, *, requested_by=None, statuses=None, tickers=None):
            return [
                t
                for t in tracked_tickers
                if (requested_by is None or t.requested_by == requested_by)
                and (statuses is None or t.status in statuses)
                and (tickers is None or t.ticker in tickers)
            ]

        async def lookup_ticker(self, ticker):
            # Not tracked yet but on the provider: the lookup's confirm-and-add state.
            return TickerLookup(
                ticker=ticker,
                outcome="available",
                status=None,
                display_name="iShares Core S&P 500",
                currency="EUR",
                exchange="GER",
                asset_type="ETF",
            )

    return Auth(), Portfolio()
