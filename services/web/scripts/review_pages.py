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
    # Clicked once the page has loaded, for a state only a click reaches (an open dialog).
    click: str | None = None


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
        Account,
        Backtest,
        BacktestAssetStatus,
        BacktestPeriodReturn,
        BacktestRow,
        Broker,
        Holding,
        HoldingAccount,
        Holdings,
        HoldingsTotals,
        PortfolioReturns,
        PortfolioSeriesPoint,
        Position,
        Return,
        TickerLookup,
        TickerMapping,
        TrackedTicker,
        Transaction,
        UnmappedInstrument,
    )
    from web.portfolio_view import aggregate_portfolio_returns

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

        async def backtest(self, *, assets, period):
            """The review holdings' mix (VWCE.DE and AAPL; NEWCO.DE has no price, so the page
            leaves it out). AAPL's history is too short for 5Y, so neither it nor the mix has
            a 5Y figure. 7203.T was never requested: it is there so the review shows the list of
            assets portfolio couldn't backtest."""
            returns = {
                None: ("0.0123", "0.0410", "-0.0215", "0.1180", "0.2402", None, "0.3071"),
                "VWCE.DE": ("0.0141", "0.0388", "-0.0102", "0.1305", "0.2650", "0.6120", "0.3390"),
                "AAPL": ("0.0087", "0.0452", "-0.0440", "0.0930", "0.1910", None, "0.2442"),
            }
            starts = (
                date(2026, 9, 9),
                date(2026, 7, 9),
                date(2026, 4, 9),
                date(2025, 10, 9),
                date(2024, 10, 9),
                date(2021, 10, 9),
                date(2021, 11, 1),  # Max: AAPL's first day in the review data
            )

            def row(ticker, weight):
                periods = {
                    p: BacktestPeriodReturn(
                        start=start,
                        end=today,
                        covered=figure is not None,
                        return_pct=None if figure is None else Decimal(figure),
                        gaps=0,
                    )
                    for p, start, figure in zip(
                        BACKTEST_PERIODS, starts, returns[ticker], strict=True
                    )
                }
                return BacktestRow(ticker=ticker, weight=weight, periods=periods)

            total = sum((w for _, w in assets), Decimal("0"))
            return Backtest(
                end_date=today,
                mix=row(None, Decimal("1")),
                assets=tuple(row(t, w / total) for t, w in assets),
                growth=None,
                statuses=tuple(BacktestAssetStatus(t, "included", None) for t, _ in assets)
                + (
                    BacktestAssetStatus(
                        "7203.T", "no_fx_rate", "Stakehold has no JPY exchange rates."
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
    if page_spec.click:
        page.click(page_spec.click)

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
