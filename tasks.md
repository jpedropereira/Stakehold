# Tasks

Roadmap for the portfolio-view chunk: one page that shows everything a user holds across all of
their accounts, summed per ticker. 120 units of IWDA in account A and 20 in account B show as 140
units of IWDA, with a per-account breakdown on request. A returns-over-time chart for the whole
portfolio follows the holdings table.
The previous chunks are archived in [`docs/roadmap-archive/`](docs/roadmap-archive/):
[walking skeleton](docs/roadmap-archive/walking-skeleton.md),
[UI/UX](docs/roadmap-archive/ui-ux.md) and
[ticker onboarding](docs/roadmap-archive/ticker-onboarding.md).

**Task IDs are prefixed `PF-`** so they can't be confused with the walking-skeleton's bare
"task N" references, the UI chunk's `UI-N` or the ticker-onboarding chunk's `TK-N`.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency.

## Agreed requirements

- **URLs.** The combined view lives at `/portfolio`, with its own top-level "Portfolio" nav link.
  The per-account holdings page moves from `/portfolio?account=X` to
  `/accounts/holdings?account=X`, and old links redirect.
- **Matching.** Holdings merge on `canonical_ticker`. Different listings of one security (e.g.
  `IWDA.AS` and `SWDA.L`) stay as separate rows.
- **Columns.** Each row shows units, average cost (EUR), market value (EUR), weight, unrealized
  return and total return (€ and %), growth since inception (%), native-currency figures, and a
  per-account breakdown.
- **Mixed currencies.** If two accounts recorded a ticker in different trade currencies, the
  row's native cells read "Mixed currencies" and the breakdown shows each account's own native
  figures. EUR figures are always summed.
- **Summing happens in the portfolio service**, in a new read-only `GET /holdings?user_id`,
  computed on read from the existing snapshots. web makes one call rather than two per account,
  and PF-5's time series needs a server-side path anyway, since fetching every account's full
  return history into web is what UI-11 avoided as too expensive.
- **Chart.** The returns-over-time chart for the whole portfolio came after the holdings table
  (PF-5, PF-6).
- **Returns as a percentage.** The combined page offers the same percentage views as the
  per-account page after #65:
  - per row, unrealized and total return % over cost basis (PF-1, PF-4), plus that holding's
    growth since inception %, time-weighted across every account holding it (PF-5, PF-6),
  - for the whole portfolio, the total return % tile, plus a "Growth since inception" tile and a
    "Growth %" chart toggle. Growth is time-weighted, so money added or taken out doesn't move it
    (PF-5, PF-6).

  Growth is computed by portfolio, as #65's `GET /returns/growth` is for one account, because
  its money flows need `mappings.resolve`.

## Constraints that apply to every task

- **No schema change is planned.** Everything is computed on read from `position_snapshots` and
  `return_snapshots`. If a task turns out to need a table or column, stop and agree it with the
  user first, per `.claude/rules/data-schema.md`.
- **User isolation stays in `accounts/repository.py`.** The cross-account read lists the user's
  accounts through it, and never queries snapshots by `user_id` in some new way.
- **Percentages are recomputed from summed EUR figures and never averaged**, the same rule as
  web's `aggregate_portfolio_returns`. Cross-account sums are keyed on (account, ticker) until the
  final merge, so the same ticker in two accounts can't collapse into one account's row. That
  collapse is the bug UI-11 documented.
- **Call path stays web → portfolio → data-store.** New pages get their own routers, as
  `api/portfolio_page.py` already does, so web's `api/routes.py` (56 KB) stays under 64 KB.
- **Tests first**, per `.claude/rules/testing.md`. Each service's `docs/architecture.md` (and
  web's `docs/ui-architecture.md`) is updated in the same change whenever a task is
  architectural, per `.claude/rules/architecture.md`.
- **Brand.** Forest and oxblood are used only on gain and loss figures, every number is set in
  `--font-mono`, and tables stack below 600 px. Copy follows chapter IV of the charter.

## Status table

| ID | Task | Depends on | Status |
|----|------|-------------|--------|
| PF-0 | [Roadmap housekeeping](#pf-0-roadmap-housekeeping) | — | done |
| PF-1 | [portfolio: `GET /holdings` across accounts](#pf-1-portfolio-get-holdings-across-accounts) | — | done |
| PF-2 | [web: move per-account holdings to `/accounts/holdings`](#pf-2-web-move-per-account-holdings-to-accountsholdings) | — | done |
| PF-3 | [web: portfolio client for `/holdings`](#pf-3-web-portfolio-client-for-holdings) | PF-1 | done |
| PF-4 | [web: `/portfolio` combined page](#pf-4-web-portfolio-combined-page) | PF-2, PF-3 | done |
| PF-5 | [portfolio: cross-account return series](#pf-5-portfolio-cross-account-return-series) | PF-1 | done |
| PF-6 | [web: returns chart on `/portfolio`](#pf-6-web-returns-chart-on-portfolio) | PF-4, PF-5 | done |

## Tasks

### PF-0. Roadmap housekeeping

**Status:** done

The ticker-onboarding chunk moved verbatim (apart from relative links) to
[`docs/roadmap-archive/ticker-onboarding.md`](docs/roadmap-archive/ticker-onboarding.md), and this
file was started. The Deferred list is carried over, except for the cross-account return series,
which PF-5 and PF-6 now cover.

### PF-1. portfolio: `GET /holdings` across accounts

**Status:** done

`GET /holdings?user_id` returns `{"holdings": [...], "totals": {...} | null}`, computed on read.
`holdings/aggregator.py` is the pure `aggregate_holdings(per_account) -> HoldingsResult`, and
`holdings/flows.py`'s `list_holdings` is the orchestration around it: it lists the user's
accounts through `accounts/repository` and reads each one's latest position and return rows
through the existing `list_latest_snapshots`. `jobs.get_holdings` and the route call it.
`list_holdings` went into `holdings/flows.py` rather than `pipeline.py` (50.9 KB), where PF-5's
orchestration is planned too.

Aggregation rules, as built:
- **Rows.** A ticker gets a row if any account has an open position in it, `quantity != 0`. That
  is web's `filter_open_positions` rule: it keeps a negative quantity (a replay anomaly) visible.
  The task first said `> 0`, which isn't what web does. Only open positions feed the row.
- **Joined on (account, ticker).** Units and cost come from each account's position, values from
  that account's own latest return row, as web's `build_holdings` joins them.
- **Summed per ticker:** `quantity`, `cost_basis_eur`, and, when priced, `market_value_eur`,
  `unrealized_return_eur`, `dividends_received_eur` and `total_return_eur`.
- **Average cost** is `sum(cost_basis_eur) / sum(quantity)`, null when the units net to 0.
- **Percentages** are `sum(return) / sum(cost)`, null when the cost is 0.
- **Native figures.** If every account agrees on `native_currency`, `average_cost_native` is
  weighted by quantity and `market_value_native` is summed. Otherwise both are null and the row
  reads `mixed_currency = true`.
- **Unpriced holdings.** If any contributing account has no return row yet, the row still counts
  its units and cost, its value fields are null and it reads `priced = false`.
- **Price date** is the newest return date among the row's accounts.
- **Breakdown.** `accounts` lists every account holding it, in label order: `account_label`,
  `quantity`, `native_currency`, `average_cost_native`, `cost_basis_eur`, `market_value_native`,
  `market_value_eur` and `unrealized_return_eur`. `market_value_native` was added so the
  breakdown can show each account's own native figures, as the mixed-currencies requirement says.
- **Matching the home page.** Closed positions' dividends do count today: web's
  `summarize_latest_returns` sums every latest return row, closed tickers included, and the home
  page sums those per account. Totals built from the open rows would leave them out, so the
  response carries a separate `totals` (market value, cost basis, dividends, total return € and
  %, price date) on the home page's rule. It is null when no account has a return row.

Tests: `tests/unit/holdings/test_{aggregator,flows}.py`, `tests/unit/test_jobs_holdings.py`,
`tests/unit/api/test_routes_holdings.py`, and `tests/integration/test_holdings.py` (two accounts
summed, another user's account never included, no accounts gives an empty result).

Files: new `holdings/{__init__,aggregator,flows}.py`, `jobs.py`, `api/{routes,models}.py`;
`docs/architecture.md`, `docs/flows.md`. portfolio has no `README.md`.

### PF-2. web: move per-account holdings to `/accounts/holdings`

**Status:** done

- The router `api/portfolio_page.py` (which held the `GET /portfolio` handler and
  `_load_portfolio_view`, moved out of `routes.py` in #65) is now `api/holdings.py`, serving
  `/accounts/holdings`. The loader is `_load_holdings_view`.
- `templates/portfolio.html` is now `account_holdings.html`.
- `GET /portfolio?account=X` answers with a 308 to `/accounts/holdings?account=X`, passing the
  query string on unchanged and asking nobody who is logged in (the target page does). It lives
  in a new `api/portfolio.py`, so PF-4 extends that file rather than moving the route. Without
  `account`, `/portfolio` is still a 422 until PF-4.
- Every link now points at the new URL: the home page's account rows, the account label on
  `/accounts`, "View holdings" after an import, `partials/_account_header.html` (Holdings) and
  `scripts/review_pages.py`. A unit test checks that no template links to `/portfolio`.
  - Two items in the original list needed no change. The account switcher submits to the
    current path, so it moved with the page. No deletion redirect pointed at `/portfolio`.

Files: `api/{portfolio_page→holdings,portfolio,app}.py`,
`templates/{portfolio→account_holdings,home,accounts,transactions_import,transactions}.html`,
`partials/_account_header.html`, `scripts/review_pages.py`; `docs/architecture.md`,
`docs/ui-architecture.md`, `docs/smoke-test.md`.

### PF-3. web: portfolio client for `/holdings`

**Status:** done

`list_holdings(user_id)` on the `PortfolioClient` protocol and `HttpPortfolioClient`, calling
PF-1's `GET /holdings` and returning `Holdings(holdings, totals)`: tuples of `Holding`, each with
its `HoldingAccount` breakdown, and `HoldingsTotals` or None. Fields mirror PF-1's response
models one for one, nullable where PF-1 serves null; Decimals arrive as strings. Portfolio's
order (by ticker, breakdown by label) is kept. A malformed body, including a `priced` or
`mixed_currency` that isn't a real boolean, is `PortfolioUnavailable`; no new error type.

The task first said "the fake client used in tests gets the same method". There is no shared
fake: each page's test file defines its own with only what that page calls, so PF-4's test file
adds `list_holdings` to its fake.

Tests: `tests/unit/test_portfolio_client_holdings.py`. No integration test yet, since no page
calls it; PF-4's page test in `tests/integration/test_portfolio_flow.py` runs it against
portfolio's real app.

Files: `portfolio_client/{base,http_client}.py`; `docs/architecture.md` (the module list's
`portfolio_client` entry, as web's doc has no endpoint table).

### PF-4. web: `/portfolio` combined page

**Status:** done

As built:
- `api/portfolio.py`'s one `/portfolio` handler keeps the 308 when `account` is present (an
  empty `account` is a 422) and renders the combined page otherwise. It calls `require_user`
  itself after the redirect check, so the redirect still makes no `auth` call. As a result,
  overriding `require_user` doesn't reach the page, and the integration tests override
  `get_auth_client` instead.
- It calls `list_accounts`, `list_holdings` and `list_unmapped_instruments` concurrently, each
  degrading on its own. The account list is there only because `/holdings` is empty both for no
  accounts (the first-run card) and for accounts holding nothing ("No holdings yet").
- A "Portfolio" nav link sits between Overview and Accounts. The phone nav's gap tightened, so
  an admin's five links fit at 360px.
- The page shows:
  - the "Prices as of" line and four tiles (market value, cost basis, total return € and %,
    dividends), from `totals`,
  - the unmapped warning over the user's whole list, de-duplicated across brokers
    (`portfolio_view.unmapped_across_brokers`). The markup moved into
    `partials/_unmapped_notice.html`, which both holdings pages include,
  - the holdings table: Ticker | Units | Avg cost | Market value | Weight | Unrealized € / % |
    Total return € / %. Native figures are on a muted second line, only off EUR, and read
    "Mixed currencies" when the accounts' currencies differ,
  - under each row, a full-width breakdown row, including for single-account holdings: a
    `<details>` ("In N accounts") holding a stackable table of each account's units, native
    average cost, cost basis, market value and unrealized €, with links to that account's page.
- Weight is computed over priced rows only (`portfolio_view.build_portfolio_holdings`).
  Unpriced rows use `ticker_status.load_statuses`.
- A failed `/holdings` shows a notice and no table or tiles. A failed account list is never the
  first-run card.
- PF-2's "no template links to `/portfolio`" test now checks for `/portfolio?`.
- `scripts/review_pages.py` covers `portfolio` and `portfolio-breakdown` (opened).
- Tests:
  - `tests/unit/test_portfolio_page.py`,
  - `tests/unit/test_portfolio_view.py`,
  - `tests/unit/test_responsive_a11y.py`,
  - `tests/unit/test_account_holdings_page.py`,
  - two in `tests/integration/test_portfolio_flow.py`: no accounts, and one ticker bought in two
    accounts showing as one row. These also cover PF-3's client against portfolio's real app.

Files: `api/portfolio.py`, `portfolio_view.py`, new `templates/portfolio.html` and
`partials/_unmapped_notice.html`, `account_holdings.html`, `base.html`, `static/web.css`,
`scripts/review_pages.py`; `docs/architecture.md`, `docs/ui-architecture.md`.

### PF-5. portfolio: cross-account return series

**Status:** done

As built:
- `returns/performance.py` keys rows and flows on (account, ticker), and `TradeFlow` gained
  `account_id`. `compute_series` is the single pass, returning `PortfolioPoint`s with the summed
  EUR figures, `total_return_pct` and `growth_pct`. `compute_growth` is now its growth-only
  projection, so `GET /returns/growth` is unchanged and its existing tests pin that.
  `compute_ticker_growth` gives `by_ticker`. It includes closed tickers, sorted by ticker.
- `pipeline.list_trade_flows(conn, account, mappings=...)` was extracted from `list_growth`, so
  both endpoints resolve trades through one function.
- `holdings/flows.list_portfolio_returns` reads each account's full history (never ranged), then
  crops `series` to `start`/`end`.
- `jobs.get_portfolio_returns` logs "no rows in any account" differently from "no days in the
  requested range".
- Tests: `tests/unit/returns/test_performance.py`, `tests/unit/pipeline/test_pipeline_growth.py`,
  `tests/unit/holdings/test_flows.py`, `tests/unit/test_jobs_portfolio_returns.py`,
  `tests/unit/api/test_routes_returns.py`, `tests/integration/test_portfolio_returns.py`.

The original spec follows.

`GET /returns/portfolio?user_id[&start&end]` returns an object with two parts:
- **`series`**, one point per date:
  - summed market value, cost basis, dividends and total return in EUR,
  - `total_return_pct`, recomputed from the sums,
  - `growth_pct`, the whole portfolio's time-weighted growth since inception.
- **`by_ticker`**, one `{canonical_ticker, growth_pct}` per ticker: that holding's latest
  growth since inception across every account holding it.

`start` and `end` only crop `series`. Growth is always computed from the first priced day, so a
shorter range never restarts it at zero, and `by_ticker` ignores the range.

One series carries the EUR figures and growth together, so the chart needs one call and no join
by date. #65's per-account page needed that join only because its euro series was built in web.

**Why per-row growth lives here, not in PF-1's `/holdings`.** It needs each ticker's full
history and its trades, the same read the series makes. `/holdings` reads only the latest rows
and stays cheap. Keeping growth in a separate call also lets it fail on its own: the table still
renders, and the growth cells show "—".

**Growth across accounts.**
- It reuses `returns/performance.py`'s `compute_growth`, run over every account's return rows
  and `BUY`/`SELL` flows at once. It is never an average of per-account growth figures, which is
  not a time-weighted return.
- `compute_growth` groups rows and lands flows by `canonical_ticker` alone. Given two accounts'
  rows, the same ticker would collapse into one forward-filled series, which is the bug UI-11
  documented. So it must first be keyed on (account, ticker):
  - `TradeFlow` gains `account_id`,
  - the grouping and the flow lookup use the pair,
  - existing `GET /returns/growth` results stay unchanged (a regression test pins this).
- Each account's transactions resolve through `mappings.resolve` with that account's own broker,
  as `pipeline.list_growth` does.
- A sell in one account and a buy in another count as money out and money in. That is correct
  for a time-weighted return, and a test should cover it.
- **Per ticker:** `compute_growth` runs over that ticker's rows and flows from every account,
  still keyed on (account, ticker), and the last point is the figure. A ticker sold out and
  bought back holds flat while nothing is held, as account growth does.

**Forward-fill** happens in Python, in the same pass `compute_growth` already makes, rather than
in SQL. Record why. No new table.

**Placement.** `pipeline.py` (51 KB) and `jobs.py` (52 KB) are near the limit, so the
orchestration goes in `holdings/flows.py`.

Tests:
- one account, which matches `GET /returns/growth`,
- the same ticker in two accounts, for both the portfolio series and `by_ticker`,
- a ticker held in only one account, whose `by_ticker` figure matches that account's growth
  over that ticker alone,
- money moving between accounts,
- `start` and `end` cropping `series` without changing any growth figure,
- an account with no rows yet,
- an integration test that checks another user's data doesn't leak in.

Files: `returns/performance.py`, `holdings/flows.py`, `jobs.py`, `api/{routes,models}.py`;
`docs/architecture.md`, `docs/flows.md`.

### PF-6. web: returns chart on `/portfolio`

**Status:** done

As built:
- `portfolio_client` gained `list_portfolio_returns` (`GET /returns/portfolio`, no `start` or
  `end`: the range buttons crop on the client) and `PortfolioReturns`, `PortfolioSeriesPoint`
  and `TickerGrowth`.
- `api/portfolio.py` makes it a fourth concurrent call, degrading on its own.
- `portfolio_view.build_portfolio_holdings` takes `by_ticker` and sets each row's `growth_pct`,
  None unless the row is `priced`. `build_portfolio_chart_payload` builds the island straight
  from the series, in the per-account island's shape, so `static/portfolio_chart.js` is reused
  unchanged.
- The page adds:
  - a "Growth since inception" tile between Total return and Dividends,
  - a Growth % column after Total return,
  - a "Returns over time" card under the holdings, with the Total return, Market value and
    Growth % views and the 1M/6M/1Y/All ranges.
- **Changed from the spec below:** a failed call can't just drop the Growth % button. The
  chart, the tile and the column all come from that one call, so the chart card shows its own
  notice instead, and the tile and every growth cell show "—". The rest of the page is
  unaffected, and no "may be incomplete" banner shows. With a series, the Growth % button always
  shows, since every point carries growth. An empty series reads "No return history yet".
- **Layout:** ten columns don't fit the 880px card, so `/portfolio` takes the full content
  width (`.content-wide`), and the table sits in a focusable `.table-scroll` region. Between
  phone and desktop widths it scrolls in its box. This also fixes PF-4's nine-column table,
  which made the whole page scroll sideways at tablet widths.
- `review_pages.py` gained a `portfolio-growth` page (all 96 checks pass). The integration test
  checks that portfolio's real empty series reads "No return history yet".

The original spec follows.

Reuse `static/portfolio_chart.js` and the JSON data island pattern, fed from PF-5 through a new
client method. The island carries `market_value_eur`, `total_return_eur` and `growth_pct`, as
#65 defined it. `by_ticker` is joined onto the holdings table rows by `canonical_ticker`, in a
pure function in `portfolio_view.py`.

The toggles work as on the per-account page:
- views: Total return, Market value, and Growth %,
- ranges: 1M, 6M, 1Y and All.

Growth figures:
- The "Growth since inception" tile is added here, from the series' last point, because it
  comes from the same call.
- A "Growth %" column is added to the holdings table, after Total return.
- A row shows "—" when it is unpriced or partly priced (`priced = false`, so a growth figure over
  only some accounts is never shown as the holding's), or when `by_ticker` has no entry for it.
- If the call fails, the tile and every growth cell show "—" and the Growth % button is dropped,
  but the rest of the holdings table is unaffected.

Files: `portfolio_client/*`, `portfolio_view.py`, `api/portfolio.py`, `templates/portfolio.html`;
`docs/architecture.md`, `docs/ui-architecture.md`.

## Known limitations

- **Holdings merge only by `canonical_ticker`.** Different listings of one security stay as
  separate rows.
- **Portfolio growth inherits #65's limits** (`portfolio/docs/flows.md`, "Growth since
  inception"):
  - a dividend paid after a full close is missed,
  - stale rows after a failed rebuild make growth dip,
  - rows and transactions are two separate reads.

## Deferred

Carried over from the earlier chunks, each needing a new `portfolio` endpoint (and possibly a
schema discussion per `.claude/rules/data-schema.md`):

- Editing or deleting a transaction.
- Renaming or deleting an account.
- Pagination or filtering of transactions, if `GET /transactions` doesn't already support it.
- A list of the currencies `data-store` tracks (its `currencies.yaml`), so manual entry can
  suggest exactly those (UI-9 suggests a fixed list instead).
- Unmapped instruments per account, not per user (UI-7 filters by broker, so two accounts at one
  broker share a list).
- Rescaling prices quoted in minor currency units (`GBp`, `ZAc`, `ILA`).

New in this chunk:

- Merging different listings of one security (e.g. by ISIN) into one portfolio row.
