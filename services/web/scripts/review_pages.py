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
- no console errors, CSP violations included;
- for a section switch (UX-10): the page keeps its place, the URL records the choice, focus
  lands on the new selection, the section's charts are redrawn, and Back undoes it.

The run exits non-zero if any check fails.

The app runs in-process against fake auth and portfolio clients (review_fakes.py), so the run
needs no stack and never touches a database, and the pages show the same data every run. The
data is chosen to
exercise every state worth seeing: a gain, a loss, a holding whose prices are still loading, a
non-EUR transaction, a long broker reference, an instrument waiting for a mapping, return
history for the chart, and tracked tickers in every status.
"""

import argparse
import socket
import sys
import threading
import time
from dataclasses import dataclass
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
    # UX-7: after the clicks and any swap, open every fold (`details.fold`) as a reader would,
    # by clicking each visible closed summary, outer before inner, until none is left.
    open_folds: bool = False
    # A section switch's option, clicked after `click` (UX-10): it swaps only its own section,
    # and the swap is checked (_swap_problems). The screenshot shows the swapped page.
    swap: str | None = None


PAGES = [
    Page("login", "/login", "anon"),
    Page("signup", "/signup", "anon"),
    Page("overview", "/", "admin"),
    Page("overview-market-value", "/", "admin", click="[data-chart-view=market_value_eur]"),
    # The header's account menu (UX-4), open: with Admin, and without it for a user.
    Page("account-menu-admin", "/", "admin", click=".account-menu > summary"),
    Page("account-menu-user", "/", "user", click=".account-menu > summary"),
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
    Page(
        "transactions-edit",
        "/transactions/edit?account=main&ref=c0a8f5e2-9b1d-4e7a-8f3c-2d6b9e1a7f40",
        "admin",
    ),
    Page("transactions-new", "/transactions/new?account=main", "admin"),
    Page("transactions-import", "/transactions/import?account=main", "admin"),
    Page("tickers", "/tickers", "admin"),
    Page("tickers-user", "/tickers", "user"),
    Page("tickers-new", "/tickers/new?symbol=SXR8.DE", "user"),
    # Back from an add (UX-11): the banner, and the new row in the list beside the form.
    Page("tickers-new-added", "/tickers/new?added=NEWCO.DE", "user"),
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
    # LF-1: the saved labs fold open after a save, with labs-edited's mix marked shown; saving
    # that mix again under its name (the prefilled field), which asks to replace; the delete's
    # confirmation page, and its dialog over /labs.
    Page("labs-saved", "/labs?ticker=VWCE.DE&weight=70&ticker=AAPL&weight=50&saved=1", "user"),
    Page(
        "labs-save-conflict",
        "/labs?ticker=VWCE.DE&weight=70&ticker=AAPL&weight=50",
        "user",
        click="form.labs-save button[type=submit]",
    ),
    Page("labs-saved-delete", "/labs/saved/delete?name=Core", "user"),
    Page("labs-saved-delete-dialog", "/labs?lab_deleted=1", "user", click="[data-confirm]"),
    # UX-8: a long mix scrolls in its rows box. Rows added in place make labs_form.js mark the
    # box a region; a 12-row URL the fake doesn't track makes the server do it, with an error
    # under every row and the table keeping the last mix.
    Page("labs-long-mix-added", "/labs", "user", click=("[data-labs-add]",) * 9),
    Page(
        "labs-long-mix",
        "/labs?"
        + "&".join(f"ticker=LONG{i}&weight=8" for i in range(12))
        + "&last_ticker=VWCE.DE&last_weight=60&last_ticker=AAPL&last_weight=40",
        "user",
    ),
    # UX-8: the risk figures' fold opened by hand on the default page.
    Page("labs-risk-open", "/labs", "user", click=".labs-risk-fold > summary"),
    # UX-7: every fold open, with NEWCO.DE, which the fake can't backtest over every period.
    Page(
        "labs-folds-open",
        "/labs?ticker=VWCE.DE&weight=60&ticker=NEWCO.DE&weight=40",
        "user",
        open_folds=True,
    ),
    # The longest period, and an asset's line shown beside the mix's (LB-8).
    Page("labs-max", "/labs?period=Max", "user"),
    # AAPL doesn't cover 5Y, so the mix's figure and line leave it out, and say so (LB-10).
    Page("labs-left-out", "/labs?period=5Y", "user"),
    Page("labs-asset-line", "/labs", "user", click='.labs-series input[data-series="AAPL"]'),
    # UX-10: each switch swaps its own section in place.
    Page("labs-period-swap", "/labs", "user", swap='#labs-periods a[href*="period=5Y"]'),
    Page(
        "labs-window-swap",
        "/labs",
        "user",
        click=".labs-risk-fold > summary",
        swap='#labs-risk-windows a[href*="risk_window=3Y"]',
    ),
    Page(
        "risk-ex-ante-window-swap",
        "/risk/ex-ante",
        "user",
        swap='#risk-ex-ante .segmented a[href*="ex_ante_window=5Y"]',
    ),
    # UX-7: the same swap, then every fold opened, so the swapped-in charts draw on opening.
    Page(
        "risk-ex-ante-window-swap-folds-open",
        "/risk/ex-ante",
        "user",
        swap='#risk-ex-ante .segmented a[href*="ex_ante_window=5Y"]',
        open_folds=True,
    ),
    Page(
        "risk-ex-ante-exclude-swap",
        "/risk/ex-ante?ex_ante_window=Max",
        "user",
        swap='#risk-ex-ante p a[hx-get*="exclude="]',
    ),
    Page(
        "risk-ranking-order-swap",
        "/risk/what-if?rank_weight=5",
        "user",
        swap='#risk-ranking .segmented a[href*="rank_by=diversification_ratio"]',
    ),
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
    # UX-6: a link to an old in-page section goes on to its sub-page (risk_anchor.js), so this
    # one should show Concentration, not ex-post.
    Page("risk-old-anchor", "/risk#risk-concentration", "user"),
    # RK-14: the benchmark field, IWDA.AS's long history covering every relative figure, a
    # ticker the fake doesn't track (the group says why once), and an over-long field.
    Page("risk-benchmark", "/risk?benchmark=IWDA.AS", "user"),
    Page("risk-benchmark-unavailable", "/risk?benchmark=NOPE.XX", "user"),
    Page("risk-benchmark-too-long", "/risk?benchmark=ABCDEFGHIJKLMNOPQRSTU", "user"),
    # RK-18: the ex-ante section. 3Y (the default) names AAPL as outsized; 1Y greys the
    # decomposition and the squares; Max is limited by AAPL, and excluding it offers it back;
    # IWDA.AS adds the benchmark as its own row; 10Y is the fake's failure. RK-26's FX table
    # has AAPL's row on 3Y, its correlation greyed on 1Y, and the all-EUR note once AAPL is
    # excluded.
    Page("risk-ex-ante", "/risk/ex-ante", "user"),
    Page("risk-ex-ante-1y", "/risk/ex-ante?ex_ante_window=1Y#risk-ex-ante", "user"),
    Page("risk-ex-ante-max", "/risk/ex-ante?ex_ante_window=Max#risk-ex-ante", "user"),
    Page(
        "risk-ex-ante-excluded",
        "/risk/ex-ante?ex_ante_window=Max&exclude=AAPL#risk-ex-ante",
        "user",
    ),
    Page("risk-ex-ante-benchmark", "/risk/ex-ante?benchmark=IWDA.AS#risk-ex-ante", "user"),
    Page("risk-ex-ante-unavailable", "/risk/ex-ante?ex_ante_window=10Y#risk-ex-ante", "user"),
    # RK-25: the rolling charts. Ex-post's with two greyed months; ex-ante's with the default
    # pair (the heaviest holding against the portfolio); a pair against a ticker; a pair whose
    # benchmark side isn't tracked (its reason, no chart); a stale link whose holding is
    # excluded (the default, and a note); and 5Y's rolling Sharpe greyed throughout. UX-7 folds
    # them: these states open every fold, so each chart is drawn on opening; a chosen pair's fold
    # starts open.
    Page("risk-rolling", "/risk#risk-ex-post-rolling", "user", open_folds=True),
    Page("risk-rolling-ex-ante", "/risk/ex-ante#risk-ex-ante-rolling", "user", open_folds=True),
    Page(
        "risk-rolling-pair",
        "/risk/ex-ante?corr_a=AAPL&corr_b=VWCE.DE#risk-rolling-correlation",
        "user",
    ),
    Page(
        "risk-rolling-pair-unavailable",
        "/risk/ex-ante?benchmark=NOPE.XX&corr_a=AAPL&corr_b=NOPE.XX#risk-rolling-correlation",
        "user",
    ),
    Page(
        "risk-rolling-stale-pair",
        "/risk/ex-ante?ex_ante_window=Max&exclude=AAPL&corr_a=AAPL#risk-rolling-correlation",
        "user",
    ),
    Page(
        "risk-rolling-no-rf",
        "/risk/ex-ante?ex_ante_window=5Y#risk-ex-ante-rolling",
        "user",
        open_folds=True,
    ),
    # RK-28: the regime tables, each dimension a group closed by default; then ex-post's first
    # two groups and ex-ante's first opened. Ex-post's since 2023, partial and ongoing regimes
    # among them; ex-ante's since AAPL's 2015 start, older regimes greyed for VWCE.DE's 2019
    # start, and every one measured against IWDA.AS; an untracked benchmark; the fetch failing;
    # and no regimes loaded. UX-7 folds the block: the states that show a table open it first.
    Page("risk-regimes", "/risk#risk-ex-post-regimes", "user"),
    Page(
        "risk-regimes-open",
        "/risk#risk-ex-post-regimes",
        "user",
        click=(
            "#risk-ex-post-regimes > .fold > summary",
            "#risk-ex-post-regimes .risk-regime-group:nth-of-type(1) summary",
            "#risk-ex-post-regimes .risk-regime-group:nth-of-type(2) summary",
        ),
    ),
    Page(
        "risk-regimes-ex-ante-open",
        "/risk/ex-ante#risk-ex-ante-regimes",
        "user",
        click=(
            "#risk-ex-ante-regimes > .fold > summary",
            "#risk-ex-ante-regimes .risk-regime-group:nth-of-type(1) summary",
        ),
    ),
    Page(
        "risk-regimes-ex-ante",
        "/risk/ex-ante?ex_ante_window=Max#risk-ex-ante-regimes",
        "user",
        click="#risk-ex-ante-regimes > .fold > summary",
    ),
    Page(
        "risk-regimes-iwda",
        "/risk/ex-ante?benchmark=IWDA.AS#risk-ex-ante-regimes",
        "user",
        click="#risk-ex-ante-regimes > .fold > summary",
    ),
    Page(
        "risk-regimes-unavailable",
        "/risk?benchmark=NOPE.XX#risk-ex-post-regimes",
        "user",
        click="#risk-ex-post-regimes > .fold > summary",
    ),
    Page("risk-regimes-failed", "/risk?benchmark=REGIMES.FAIL#risk-ex-post-regimes", "user"),
    Page("risk-regimes-none-loaded", "/risk?benchmark=REGIMES.NONE#risk-ex-post-regimes", "user"),
    # RK-23: the concentration section, with the unmapped note, the currency table's two views
    # and greyed buckets; then with every "show all" open.
    Page("risk-concentration", "/risk/concentration", "user"),
    # UX-7: every fold on each sub-page opened, so nothing folded escapes the checks.
    Page("risk-folds-open", "/risk", "user", open_folds=True),
    Page("risk-ex-ante-folds-open", "/risk/ex-ante?exclude=AAPL", "user", open_folds=True),
    Page("risk-what-if-folds-open", "/risk/what-if?rank_weight=5", "user", open_folds=True),
    Page("risk-concentration-folds-open", "/risk/concentration", "user", open_folds=True),
    Page(
        "risk-concentration-all",
        "/risk/concentration",
        "user",
        # Each click opens the first one still closed: currency, sector, country and names.
        click=(".lt-more:not([open]) > summary",) * 4,
    ),
    # RK-20: the what-if. The empty form; EGLN.L pro-rata with its threshold; replacing AAPL
    # with more than it weighs (capped, no threshold); MSFT, priced through a fund, which
    # shortens the window (R14's warning and markers on every after figure); a candidate that
    # can't be measured; inline errors on every field; and the fake's failure.
    Page("risk-what-if-empty", "/risk/what-if#risk-what-if", "user"),
    Page(
        "risk-what-if-pro-rata",
        "/risk/what-if?candidate=EGLN.L&candidate_weight=10&funding=pro_rata#risk-what-if",
        "user",
    ),
    Page(
        "risk-what-if-replace-capped",
        "/risk/what-if?candidate=EGLN.L&candidate_weight=60&funding=replace:AAPL#risk-what-if",
        "user",
    ),
    Page(
        "risk-what-if-survivorship-shortened",
        "/risk/what-if?candidate=MSFT&candidate_weight=5#risk-what-if",
        "user",
    ),
    Page(
        "risk-what-if-nothing",
        "/risk/what-if?candidate=0700.HK&candidate_weight=5#risk-what-if",
        "user",
    ),
    Page(
        "risk-what-if-form-error",
        "/risk/what-if?candidate=AAPL&candidate_weight=150&funding=replace:MSFT#risk-what-if",
        "user",
    ),
    Page(
        "risk-what-if-unavailable",
        "/risk/what-if?candidate=FAIL.XX&candidate_weight=5#risk-what-if",
        "user",
    ),
    # RK-29: the ranking. The unsent form; 5% ranked by volatility and by diversification ratio
    # (a gap-flagged row, a greyed one, a too-young and an unmeasurable ticker, three past the
    # cap); 3% without a risk-free rate; 1Y's note; an inline error; nothing to rank (2%); and
    # the fake's failure (9%).
    Page("risk-ranking-empty", "/risk/what-if#risk-ranking", "user"),
    Page("risk-ranking", "/risk/what-if?rank_weight=5#risk-ranking", "user"),
    Page(
        "risk-ranking-by-ratio",
        "/risk/what-if?rank_weight=5&rank_by=diversification_ratio#risk-ranking",
        "user",
    ),
    Page("risk-ranking-no-rf", "/risk/what-if?rank_weight=3#risk-ranking", "user"),
    Page("risk-ranking-1y", "/risk/what-if?rank_weight=5&ex_ante_window=1Y#risk-ranking", "user"),
    Page("risk-ranking-form-error", "/risk/what-if?rank_weight=150#risk-ranking", "user"),
    Page("risk-ranking-nothing", "/risk/what-if?rank_weight=2#risk-ranking", "user"),
    Page("risk-ranking-unavailable", "/risk/what-if?rank_weight=9#risk-ranking", "user"),
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
    Page(
        "labs-benchmark-and-asset",
        "/labs?benchmark=IWDA.AS&period=Max",
        "user",
        click='.labs-series input[data-series="AAPL"]',
    ),
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
    // color-mix() computes to color(srgb r g b / a), its channels 0-1 (RK-18's heatmap).
    const srgb = value.match(/color\\(srgb ([^)]+)\\)/);
    if (srgb) {
      const parts = srgb[1].split(/[ \\/]+/).filter(Boolean).map(Number);
      return [parts[0] * 255, parts[1] * 255, parts[2] * 255, parts.length > 3 ? parts[3] : 1];
    }
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
    // A chart in a closed fold isn't drawn until the fold opens (UX-7): nothing to size yet.
    if (canvas.closest("details:not([open])")) continue;
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

# Only a cap on the walk, not the trap check (that is _MAX_TABS_ON_ONE). /risk passed 120 once
# UX-2 made each named ticker a tab stop; UX-6 splits it into sub-pages.
_MAX_TABS = 200
# A date/time input takes one Tab per part (day, month, year, hours, minutes...) without focus
# leaving the element, so only this many Tabs in a row on one element count as stuck.
_MAX_TABS_ON_ONE = 8


# ---- the app, on the fake upstreams (review_fakes.py) ------------------------------------------


def _serve():
    """Start the app on a free local port in a background thread; return its base URL."""
    import uvicorn
    from review_fakes import fakes
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
    auth, portfolio = fakes()
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


# Run in the page once a swap has settled: where focus went, and any canvas in the swapped
# section left without a chart.
_SWAP_STATE_JS = """
(id) => {
  const region = document.getElementById(id);
  const focused = document.activeElement;
  let focus = null;
  if (!region.contains(focused)) {
    focus = `outside the section, on ${focused.tagName.toLowerCase()}`;
  } else if (focused.closest(".segmented") && focused.getAttribute("aria-current") !== "true") {
    focus = "on an option that isn't the selected one";
  }
  const undrawn = [...region.querySelectorAll("canvas")]
    // A chart in a closed fold is drawn when the fold opens (UX-7), not on the swap.
    .filter((canvas) => !canvas.closest("details:not([open])"))
    .filter((canvas) => !(window.Chart && Chart.getChart(canvas)))
    .map((canvas) => canvas.id || canvas.dataset.rolling);
  return { focus, undrawn };
}
"""

# True once the swapped section is in and settled: not busy, and its change announced.
_SWAP_DONE_JS = """
(id) => {
  const region = document.getElementById(id);
  return region !== null && !region.hasAttribute("aria-busy")
    && !region.classList.contains("htmx-request")
    && document.getElementById("swap-status").textContent !== "";
}
"""


def _swap(page, selector) -> tuple[list[str], dict]:
    """Click a section switch's option and check the swap (UX-10). Returns the problems and what
    _back_problems needs to check Back afterwards."""
    option = page.locator(selector).first
    region = option.get_attribute("hx-target").lstrip("#")
    option.scroll_into_view_if_needed()
    before, url_before = page.evaluate("window.scrollY"), page.url
    option.click()
    page.wait_for_function(_SWAP_DONE_JS, arg=region, timeout=15_000)
    problems = []
    after = page.evaluate("window.scrollY")
    if abs(after - before) > 1:
        problems.append(f"the page moved: scrollY {before} to {after}")
    if page.url == url_before:
        problems.append("the URL didn't record the choice")
    state = page.evaluate(_SWAP_STATE_JS, region)
    if state["focus"]:
        problems.append(f"focus {state['focus']}")
    if state["undrawn"]:
        problems.append(f"no chart on {', '.join(state['undrawn'])}")
    return problems, {"url": url_before, "selector": selector}


def _back_problems(page, swapped) -> list[str]:
    """Back after a swap: htmx keeps no snapshot (base.html), so the browser reloads the URL
    before the switch, and the option clicked is no longer the selected one."""
    # Back first changes the URL in place, then htmx reloads the page: wait for that load. Not
    # by polling the page, which the reload would interrupt.
    with page.expect_event("load", timeout=15_000):
        page.go_back()
    page.wait_for_load_state("networkidle")
    if page.url != swapped["url"]:
        return [f"Back went to {page.url}"]
    current = page.locator(swapped["selector"]).first.get_attribute("aria-current")
    return ["Back didn't restore the previous choice"] if current == "true" else []


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
    problems, swapped = _swap(page, page_spec.swap) if page_spec.swap else ([], None)
    problems = [f"swap: {p}" for p in problems]
    # After the swap, so a swapped-in chart in a fold is drawn on opening, as on a load.
    if page_spec.open_folds:
        closed = page.locator("details.fold:not([open]) > summary:visible")
        while closed.count():
            closed.first.click()

    # Before the checks: the focus walk scrolls whatever it tabs into.
    page.screenshot(path=out / f"{page_spec.name}-{theme}-{width_name}.png", full_page=True)
    if not page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"):
        problems.append("scrolls sideways")
    problems += [f"table overflows its card: {f}" for f in page.evaluate(_TABLE_OVERFLOW_JS)]
    problems += [f"table scrolls in two columns: {f}" for f in page.evaluate(_SPLIT_SCROLL_JS)]
    problems += [f"chart: {f}" for f in page.evaluate(_CHART_SIZE_JS, list(CHART_HEIGHT))]
    problems += [f"tile figure: {f}" for f in page.evaluate(_TILE_FIGURE_JS)]
    problems += [f"contrast: {f}" for f in page.evaluate(_CONTRAST_JS)]
    problems += [f"focus: {p}" for p in _focus_problems(page)]
    if swapped:
        problems += [f"swap: {p}" for p in _back_problems(page, swapped)]
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
