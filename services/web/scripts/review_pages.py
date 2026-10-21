"""Screenshot every web page in both themes at every width, and check each one (UI-13, LY-1, LY-3,
LY-4, LY-6).

    uv run python services/web/scripts/review_pages.py [--out DIR] [--only NAME ...]
        [--width NAME ...]

The widths are phone (360px), tablet (768px), desktop (1280px), laptop (1440px, just under the
split breakpoint) and wide (1920px).

For a person to review, not a pixel-diff gate: the screenshots land in services/web/screenshots/
(gitignored). Alongside them, every page and variant gets the checks a screenshot can't show:

- no sideways scroll;
- no table wider than the card that holds it, unless it sits in a .table-scroll box;
- no .table-scroll box that scrolls while its .split shows two columns;
- every chart filling its .chart-frame, at a height within CHART_HEIGHT;
- every summary tile figure on one line, inside its tile's content box;
- WCAG AA contrast for every piece of visible text against what's actually behind it;
- a visible focus indicator on everything Tab reaches, and Tab never getting stuck;
- no console errors, CSP violations included.

The run exits non-zero if any check fails.

The app runs in-process against fake auth and portfolio clients, so the run needs no stack and
never touches a database, and the pages show the same data every run. The data is chosen to
exercise every state worth seeing: a gain, a loss, a holding whose prices are still loading, a
non-EUR transaction, a long broker reference, an instrument waiting for a mapping, return
history for the chart, and tracked tickers in every status.
"""

import argparse
import math
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

_WEB_DIR = Path(__file__).resolve().parents[1]
_DEFAULT_OUT = _WEB_DIR / "screenshots"

THEMES = ("light", "dark")
WIDTHS = {"phone": 360, "tablet": 768, "desktop": 1280, "laptop": 1440, "wide": 1920}


@dataclass(frozen=True)
class Page:
    name: str
    path: str
    # "anon", "user" or "admin": who is logged in when the page is opened.
    viewer: str
    # Clicked once the page has loaded, for a state only a click reaches (an open dialog). A
    # tuple is clicked in order.
    click: str | tuple[str, ...] | None = None


PAGES = [
    Page("login", "/login", "anon"),
    Page("signup", "/signup", "anon"),
    Page("overview", "/", "admin"),
    Page("overview-market-value", "/", "admin", click="[data-chart-view=market_value_eur]"),
    Page("portfolio", "/portfolio", "admin"),
    # The first holding's per-account breakdown, opened.
    Page("portfolio-breakdown", "/portfolio", "admin", click=".ticker-toggle"),
    Page(
        "portfolio-market-value", "/portfolio", "admin", click="[data-chart-view=market_value_eur]"
    ),
    Page("accounts", "/accounts", "admin"),
    Page("accounts-delete-dialog", "/accounts", "admin", click="a[data-confirm]"),
    Page("accounts-delete", "/accounts/delete?account=main", "admin"),
    Page("account-holdings", "/accounts/holdings?account=main", "admin"),
    Page(
        "account-holdings-market-value",
        "/accounts/holdings?account=main",
        "admin",
        click="[data-chart-view=market_value_eur]",
    ),
    Page("transactions", "/transactions?account=main", "admin"),
    Page(
        "transactions-delete-dialog",
        "/transactions?account=main",
        "admin",
        click="a[data-confirm]",
    ),
    Page("transactions-delete", "/transactions/delete?account=main&ref=buy-0904", "admin"),
    Page("transactions-new", "/transactions/new?account=main", "admin"),
    Page("transactions-import", "/transactions/import?account=main", "admin"),
    Page("tickers", "/tickers", "admin"),
    Page("tickers-user", "/tickers", "user"),
    Page("tickers-new", "/tickers/new?symbol=SXR8.DE", "user"),
    Page("labs", "/labs", "user"),
    # LB-7: an edited mix whose weights add up to 120%, a form with an error of every kind
    # (the table keeps the last mix that worked), and a row added in place by labs_form.js.
    Page("labs-edited", "/labs?ticker=VWCE.DE&weight=70&ticker=AAPL&weight=50", "user"),
    Page(
        "labs-errors",
        "/labs?ticker=VWCE.DE&weight=60&ticker=NOPE.XX&weight=20&ticker=AAPL&weight=0"
        "&ticker=vwce.de&weight=20&last_ticker=VWCE.DE&last_weight=60&last_ticker=AAPL"
        "&last_weight=40",
        "user",
    ),
    Page("labs-added-row", "/labs", "user", click="[data-labs-add]"),
    # The longest period, and an asset's line shown beside the mix's (LB-8).
    Page("labs-max", "/labs?period=Max", "user"),
    # AAPL doesn't cover 5Y, so the mix's figure and line leave it out, and say so (LB-10).
    Page("labs-left-out", "/labs?period=5Y", "user"),
    Page("labs-asset-line", "/labs", "user", click='.labs-series input[data-series="AAPL"]'),
    # Both assets' Max lines, labelled at their ends: AAPL's starts late, and the two end close
    # enough that their labels are pushed apart (LB-10).
    Page(
        "labs-two-lines",
        "/labs?period=Max",
        "user",
        click=(
            '.labs-series input[data-series="VWCE.DE"]',
            '.labs-series input[data-series="AAPL"]',
        ),
    ),
    # RK-12: 1Y, 3Y and Max measured, 5Y and 10Y greyed, flags and their legend, a greyed
    # Sharpe, and a holding left out.
    Page("risk", "/risk", "user"),
    # RK-14: the benchmark field, IWDA.AS's long history covering every relative figure, a
    # ticker the fake doesn't track (the group says why once), and an over-long field.
    Page("risk-benchmark", "/risk?benchmark=IWDA.AS", "user"),
    Page("risk-benchmark-unavailable", "/risk?benchmark=NOPE.XX", "user"),
    Page("risk-benchmark-too-long", "/risk?benchmark=ABCDEFGHIJKLMNOPQRSTU", "user"),
    # RK-9: the risk comparison over 3Y (AAPL's volatility has a gap note), over 10Y (not
    # covered, so no table), a mix the fake answers with no risk-free data, and the drawdown
    # chart over Max.
    Page("labs-risk-window", "/labs?risk_window=3Y", "user"),
    Page("labs-risk-uncovered", "/labs?risk_window=10Y", "user"),
    Page("labs-risk-no-rf", "/labs?ticker=VWCE.DE&weight=60&ticker=NEWCO.DE&weight=40", "user"),
    Page("labs-drawdown-max", "/labs?period=Max", "user"),
    # RK-14: the benchmark. IWDA.AS covers every window and draws its line over Max; VWCE.DE
    # (the default) starts after Max's first day, so the chart has no line and says why; a
    # ticker the fake doesn't track greys the relative figures, said once per group.
    Page("labs-benchmark", "/labs?benchmark=IWDA.AS&risk_window=3Y", "user"),
    Page("labs-benchmark-max", "/labs?benchmark=IWDA.AS&period=Max", "user"),
    Page("labs-benchmark-unavailable", "/labs?benchmark=NOPE.XX", "user"),
    Page("glossary", "/glossary", "user"),
    # Followed from a holdings page's "Total return" header: the entry it lands on is marked.
    Page("glossary-total-return", "/glossary#total-return", "user"),
    Page("admin", "/admin", "admin"),
    Page("admin-mappings", "/admin/mappings", "admin"),
    Page("forbidden", "/admin", "user"),
    Page("account-not-found", "/accounts/holdings?account=nope", "admin"),
    Page("not-found", "/no-such-page", "anon"),
]

# Run in the page. For each visible element with text of its own (and each form control), its
# text color against the first opaque background found walking up its ancestors, translucent
# ones composited on the way. Returns one entry per failure.
_CONTRAST_JS = """
() => {
  const parse = (value) => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const parts = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  };
  const over = (top, bottom) => {
    const a = top[3];
    return [0, 1, 2].map((i) => a * top[i] + (1 - a) * bottom[i]).concat(1);
  };
  const luminance = (rgb) => {
    const c = rgb.slice(0, 3).map((v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const ratio = (a, b) => {
    const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const background = (el) => {
    const layers = [];
    for (let node = el; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage !== "none" && node !== el) return { image: true };
      const color = parse(style.backgroundColor);
      if (color && color[3] > 0) {
        layers.push(color);
        if (color[3] === 1) break;
      }
    }
    // Under every layer, the canvas: html's own background or white.
    let result = [255, 255, 255, 1];
    for (const layer of layers.reverse()) result = over(layer, result);
    return { color: result };
  };
  const describe = (el) => {
    const id = el.id ? "#" + el.id : "";
    const cls = typeof el.className === "string" && el.className.trim()
      ? "." + el.className.trim().split(/\\s+/).join(".") : "";
    return el.tagName.toLowerCase() + id + cls;
  };
  const failures = [];
  for (const el of document.body.querySelectorAll("*")) {
    if (el.closest("template, script, style, option, [aria-hidden=true]")) continue;
    const control = el.matches("input:not([type=hidden]), select, textarea");
    const ownText = [...el.childNodes].some(
      (n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
    if (!control && !ownText) continue;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (rect.width <= 1 || rect.height <= 1 || style.visibility !== "visible") continue;
    const fg = parse(style.color);
    const bg = background(el);
    if (!fg || bg.image) continue;
    const size = parseFloat(style.fontSize);
    const large = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700);
    const needed = large ? 3 : 4.5;
    const measured = ratio(over(fg, bg.color), bg.color);
    if (measured < needed) {
      const text = (control ? el.value || el.name : el.textContent).trim().slice(0, 40);
      failures.push(`${describe(el)} "${text}": ${measured.toFixed(2)}:1, needs ${needed}:1`);
    }
  }
  return failures;
}
"""

# Run in the page after each Tab: what has focus, and whether it shows that it does.
_FOCUS_JS = """
() => {
  const el = document.activeElement;
  if (!el || el === document.body) return null;
  const style = getComputedStyle(el);
  const rect = el.getBoundingClientRect();
  const id = el.id ? "#" + el.id : "";
  const text = (el.textContent || el.value || el.name || "").trim().slice(0, 30);
  return {
    // The element's place in the document keeps two same-named buttons (a Delete per row) apart.
    position: [...document.querySelectorAll("*")].indexOf(el),
    key: el.tagName.toLowerCase() + id + " " + JSON.stringify(text),
    marked: (style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0)
      || style.boxShadow !== "none",
    visible: rect.width > 0 && rect.height > 0 && style.visibility === "visible",
  };
}
"""

# Run in the page. Every rendered table that reaches past its card's content edge (inside the
# padding), unless it sits in a .table-scroll box, which is meant to scroll. 1px of slack absorbs
# subpixel rounding. Returns one entry per failure.
_TABLE_OVERFLOW_JS = """
() => {
  const failures = [];
  for (const table of document.querySelectorAll("table")) {
    if (table.closest(".table-scroll")) continue;
    const card = table.closest(".card");
    const rect = table.getBoundingClientRect();
    if (!card || rect.width === 0 || rect.height === 0) continue;
    const box = card.getBoundingClientRect();
    const style = getComputedStyle(card);
    const left = box.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft);
    const right = box.right - parseFloat(style.borderRightWidth) - parseFloat(style.paddingRight);
    if (rect.left < left - 1 || rect.right > right + 1) {
      const names = table.className.trim();
      const cls = names ? "." + names.split(/\\s+/).join(".") : "";
      failures.push(
        `table${cls} ${Math.round(rect.width)}px in a ${Math.round(right - left)}px card`);
    }
  }
  return failures;
}
"""

# Run in the page. Every .table-scroll box that scrolls while its .split is in two columns: the
# split breakpoint is chosen so the table column fits its table, and the overflow check above
# skips anything in a .table-scroll box, so a breakpoint set too low would otherwise go unseen.
_SPLIT_SCROLL_JS = """
() => {
  const failures = [];
  for (const box of document.querySelectorAll(".split .table-scroll")) {
    const split = box.closest(".split");
    const columns = getComputedStyle(split).gridTemplateColumns.trim().split(/\\s+/).length;
    if (columns > 1 && box.scrollWidth > box.clientWidth + 1) {
      const label = box.getAttribute("aria-label") || "table";
      failures.push(`${label}: ${box.scrollWidth}px in a ${box.clientWidth}px box`);
    }
  }
  return failures;
}
"""

# Run in the page. Every summary tile figure that wraps onto a second line or reaches past its
# tile's content edge. A signed figure can break after its sign ("+" alone on a line), and an
# unsigned one has nowhere to break, so it runs into the tile's padding instead; the overflow
# checks above see neither, since the tile still holds it. Returns one entry per failure.
_TILE_FIGURE_JS = """
() => {
  const failures = [];
  for (const figure of document.querySelectorAll(".tile-value .mono")) {
    const tile = figure.closest(".tile");
    const style = getComputedStyle(tile);
    const edge = tile.getBoundingClientRect().right - parseFloat(style.paddingRight);
    const label = tile.querySelector(".tile-label")?.textContent.trim() || "tile";
    if (figure.getClientRects().length > 1) {
      failures.push(`${label}: ${figure.textContent} wraps`);
    } else if (figure.getBoundingClientRect().right > edge + 1) {
      const over = Math.round(figure.getBoundingClientRect().right - edge);
      failures.push(`${label}: ${figure.textContent} runs ${over}px past its tile's content`);
    }
  }
  return failures;
}
"""

# The chart's agreed height bounds: 280-480px stacked (LY-3), 320-560px in a .split column (LY-4).
CHART_HEIGHT = (280, 560)

# Run in the page. Every chart canvas that doesn't fill its .chart-frame, which means Chart.js is
# sizing it from its own width rather than from the frame, or whose height is outside the bounds.
# Returns one entry per failure.
_CHART_SIZE_JS = """
([low, high]) => {
  const failures = [];
  for (const canvas of document.querySelectorAll("canvas")) {
    const frame = canvas.parentElement;
    const rect = canvas.getBoundingClientRect();
    if (!frame.classList.contains("chart-frame")) {
      failures.push(`#${canvas.id} isn't in a .chart-frame`);
      continue;
    }
    const box = frame.getBoundingClientRect();
    if (Math.abs(rect.width - box.width) > 1 || Math.abs(rect.height - box.height) > 1) {
      failures.push(
        `#${canvas.id} is ${Math.round(rect.width)}x${Math.round(rect.height)}px` +
        ` in a ${Math.round(box.width)}x${Math.round(box.height)}px frame`);
    } else if (rect.height < low - 1 || rect.height > high + 1) {
      failures.push(`#${canvas.id} is ${Math.round(rect.height)}px tall, not ${low}-${high}px`);
    }
  }
  return failures;
}
"""

_MAX_TABS = 120
# A date/time input takes one Tab per part (day, month, year, hours, minutes...) without focus
# leaving the element, so only this many Tabs in a row on one element count as stuck.
_MAX_TABS_ON_ONE = 8


# ---- fake upstreams ----------------------------------------------------------------------------


def _fakes():
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
        DrawdownSeries,
        ExcludedHolding,
        ExPostRisk,
        ExPostWindow,
        Holding,
        HoldingAccount,
        Holdings,
        HoldingsTotals,
        PortfolioReturns,
        PortfolioSeriesPoint,
        Position,
        Return,
        RiskFigure,
        RiskFreeStatus,
        TickerLookup,
        TickerMapping,
        TrackedTicker,
        Transaction,
        UnmappedInstrument,
    )
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


def _serve():
    """Start the app on a free local port in a background thread; return its base URL."""
    import uvicorn
    from web import settings as settings_module
    from web.api.app import create_app
    from web.api.deps import get_auth_client, get_portfolio_client
    from web.settings import Settings

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    settings = Settings(
        auth_api_url="http://auth.invalid",
        internal_secret="review-script",  # pragma: allowlist secret
        portfolio_api_url="http://portfolio.invalid",
        portfolio_internal_secret="review-script",  # pragma: allowlist secret
        brand_dir=settings_module._DEFAULT_BRAND_DIR,
        cookie_secure=False,
        api_host="127.0.0.1",
        api_port=port,
        http_timeout_seconds=5.0,
    )
    auth, portfolio = _fakes()
    app = create_app(settings)
    app.dependency_overrides[get_auth_client] = lambda: auth
    app.dependency_overrides[get_portfolio_client] = lambda: portfolio
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}"


# ---- checks ------------------------------------------------------------------------------------


def _focus_problems(page) -> list[str]:
    """Tab through the page from the top; report anything focused without a visible marker, and
    a Tab that doesn't move focus on (a trap)."""
    problems, first, previous, repeats = [], None, None, 0
    for _ in range(_MAX_TABS):
        page.keyboard.press("Tab")
        focused = page.evaluate(_FOCUS_JS)
        if focused is None or focused["position"] == first:
            return problems
        repeats = repeats + 1 if focused["position"] == previous else 0
        if repeats >= _MAX_TABS_ON_ONE:
            return problems + [f"focus stuck on {focused['key']}"]
        # Every stop, a date input's inner parts included: its calendar button is one of them.
        if not focused["visible"]:
            problem = f"focus on an invisible element: {focused['key']}"
        elif not focused["marked"]:
            problem = f"no visible focus indicator: {focused['key']}"
        else:
            problem = None
        if problem and problem not in problems:
            problems.append(problem)
        first = focused["position"] if first is None else first
        previous = focused["position"]
    return problems + [f"still tabbing after {_MAX_TABS} stops"]


def _review(browser, base_url, page_spec, theme, width_name, out) -> list[str]:
    context = browser.new_context(
        color_scheme=theme, viewport={"width": WIDTHS[width_name], "height": 900}
    )
    if page_spec.viewer != "anon":
        token = f"{page_spec.viewer}-tok"
        context.add_cookies([{"name": "stakehold_session", "value": token, "url": base_url}])
    page = context.new_page()
    url = base_url + page_spec.path
    console = []

    def on_console(message):
        # An error page's own status is logged as a failed load; that's the page working.
        if message.type == "error" and message.location.get("url") != url:
            console.append(message.text)

    page.on("console", on_console)
    page.on("pageerror", lambda e: console.append(str(e)))
    page.goto(url, wait_until="networkidle")
    clicks = (page_spec.click,) if isinstance(page_spec.click, str) else page_spec.click or ()
    for selector in clicks:
        page.click(selector)

    # Before the checks: the focus walk scrolls whatever it tabs into.
    page.screenshot(path=out / f"{page_spec.name}-{theme}-{width_name}.png", full_page=True)
    problems = []
    if not page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"):
        problems.append("scrolls sideways")
    problems += [f"table overflows its card: {f}" for f in page.evaluate(_TABLE_OVERFLOW_JS)]
    problems += [f"table scrolls in two columns: {f}" for f in page.evaluate(_SPLIT_SCROLL_JS)]
    problems += [f"chart: {f}" for f in page.evaluate(_CHART_SIZE_JS, list(CHART_HEIGHT))]
    problems += [f"tile figure: {f}" for f in page.evaluate(_TILE_FIGURE_JS)]
    problems += [f"contrast: {f}" for f in page.evaluate(_CONTRAST_JS)]
    problems += [f"focus: {p}" for p in _focus_problems(page)]
    problems += [f"console: {line}" for line in console]
    context.close()
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="screenshot folder")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="review just these pages")
    parser.add_argument(
        "--width", nargs="+", choices=WIDTHS, metavar="NAME", help="review at just these widths"
    )
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    pages = [p for p in PAGES if not args.only or p.name in args.only]
    unknown = set(args.only or ()) - {p.name for p in PAGES}
    if unknown:
        parser.error(f"no such page: {', '.join(sorted(unknown))}")
    widths = [w for w in WIDTHS if not args.width or w in args.width]
    args.out.mkdir(parents=True, exist_ok=True)
    variants = len(THEMES) * len(widths)
    print(
        f"Reviewing {len(pages)} page(s) x {variants} variants ({', '.join(widths)});"
        f" screenshots to {args.out}"
    )

    base_url = _serve()
    failed = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for page_spec in pages:
            for theme in THEMES:
                for width_name in widths:
                    problems = _review(browser, base_url, page_spec, theme, width_name, args.out)
                    label = f"{page_spec.name} ({theme}, {width_name})"
                    print(f"{'FAIL' if problems else 'ok  '} {label}")
                    for problem in problems:
                        print(f"       {problem}")
                    failed += bool(problems)
        browser.close()

    total = len(pages) * variants
    print(f"Done: {total - failed} of {total} passed, {failed} with problems.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
