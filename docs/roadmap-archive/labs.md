# Labs roadmap (archived)

Completed 2026-10, tasks LB-0 to LB-10. This is the labs chunk's thread file,
`tasks-labs-thread.md`, moved here once all of its tasks were done. The content is verbatim except
for relative links, adjusted for the new location. No thread was open to take over its Deferred
list, so it stays here, for the next thread to carry forward.

---

# Tasks

Roadmap for the labs chunk: a new top-level section, **Labs**, for backtesting. It shows how the
user's current portfolio would have done over the last month, 3 months, 6 months, year, 2 years,
5 years and the longest history all its assets share. It shows this for the mix as a whole and for
each asset. The user can add or remove assets and change their weights to see how that changes
returns. Only percentage returns matter here. The portfolio's value plays no part.
Commit a726e51 removed "growth since inception" from the portfolio views so that this kind of
analysis could live here instead.
The previous chunks are archived in [`docs/roadmap-archive/`](./):
[walking skeleton](walking-skeleton.md),
[UI/UX](ui-ux.md),
[ticker onboarding](ticker-onboarding.md),
[portfolio view](portfolio-view.md) and
[screen layout](screen-layout.md).

**Task IDs are prefixed `LB-`** so they can't be confused with the walking-skeleton's bare
"task N" references or the earlier chunks' `UI-N`, `TK-N`, `PF-N` and `LY-N`.

**Status** is one of:
- `done`: implemented, tested, merged.
- `ready`: every dependency is `done`, so it can be started now.
- `blocked`: waiting on at least one dependency.

## Agreed requirements

- **URL and nav.** Labs lives at `/labs`, with its own top-level "Labs" nav link.
- **Starting point.** With no parameters, the mix is the user's current holdings (from
  `GET /holdings`), weighted by market value, as `/portfolio` computes weight. Unpriced holdings
  have no weight, so they are left out, and a note names them.
- **Periods.** 1M, 3M, 6M, 1Y, 2Y, 5Y and Max.
  - **End date.** Every period ends on the latest date for which every asset in the mix has data.
  - **Start date.** Each period starts that many calendar months earlier. The day of the month is
    clamped, as `rangeStart` in `portfolio_chart.js` already does.
  - **Max** is each asset's whole history. The mix's Max starts on the earliest asset's first
    date, and each other asset joins on its own first date. *Changed in LB-9:* Max was the
    longest history all the assets shared, so one recently onboarded ticker cut every row's Max
    to its few weeks.
  - **Short history.** A period longer than an asset's history shows "Not enough history" for
    that asset. The mix's figure and growth line for that period use only the assets that
    cover it, re-weighted to 100%, and the page names the assets left out. The mix has no
    figure only when no asset covers the period. *Changed in LB-9:* the rule was that the mix
    had a figure only when every asset covered the period. One ticker onboarded a month
    earlier then emptied the mix's line and figures for 3M to 5Y, and the chart showed only
    asset lines.
  - **An asset joining Max rebalances the mix.** On its first date, the mix's value is split
    again by the target weights, normalized over every asset present by then. Between those
    dates it is buy and hold. Agreed in LB-9. Keeping Max to the shared history was the
    assumption until then. Buying the new asset with fresh money beside the drifted holdings was
    considered and rejected: the return would need time-weighting to stay a percentage.
- **Buy and hold.** The mix is bought at its weights on the period's first day. Weights then
  drift with prices, with no rebalancing. Each period starts again from the target weights on its
  own first day. With Gᵢ(t) as asset i's growth since the start date s, the mix grows as
  V(t) = Σ wᵢ · Gᵢ(t) / Gᵢ(s). The one rebalance is an asset joining Max (Short history,
  above).
- **Total return in EUR.** Each asset's daily return includes dividends: data-store's
  `market_returns.simple_return`, chained. It is converted to EUR through `fx_rates`, so a USD
  asset's return includes the USD/EUR move. Returns are percentages only.
- **Editing.** The user can add any ticker that data-store has history for (the
  `GET /instruments/tickers` list), remove assets, and change weights. Weights are entered as
  percentages. A ticker that isn't tracked links to "Add a ticker". A ticker that is tracked but
  has no returns yet (still onboarding) shows "No history yet" and is left out of the mix.
- **Weights that don't add up to 100%.** They are normalized, and the page shows the weights
  it actually used. Agreed in LB-7. Rejecting the mix with a form error was considered and
  rejected.
- **Not saved.** The mix is held in the query string, so a lab can be bookmarked or reloaded, but
  nothing is stored. "Reset to my portfolio" is `/labs` with no parameters. Saving labs is
  deferred.
- **Outputs.** There are two:
  - A table: a row for the mix and a row for each asset, with the seven periods as columns.
  - A growth chart: the mix's cumulative % growth over a chosen period, with each asset's growth
    available as a line. The mix's line is always the main one. Asset lines are off until the
    user picks them (LB-10).

  Annualized returns and a side-by-side comparison with the unedited portfolio were considered
  and deferred.

## Constraints that apply to every task

- **No schema change, in any service.** Everything this chunk needs is already stored. If a task
  turns out to need a table or column change, stop and agree it first
  (`.claude/rules/data-schema.md`).
- **web talks only to portfolio** (`services/web/docs/architecture.md`, "Talks to"). Price and FX
  history reach web through a new portfolio endpoint, never through a data-store client in web.
- **Returns come from `simple_return`, never from close prices.** That column already accounts
  for dividends and splits. Adding a split correction on top double-adjusts it. Close prices
  still contain bad spikes that the return calculation discards (data-store `schema.md`,
  `market_returns`).
- **A NULL return is a gap, not 0.** It marks a ticker's first day or a day data-store judged
  implausible.
- **Brand.** Use tokens only, with no hex values. Every number is in `--font-mono`. Gold is the
  only accent. Forest and oxblood are used only on gains and losses.
- **Tests first,** per `.claude/rules/testing.md`. The calculator is tested with hand-computed
  fixtures. Integration tests run only against `docker-compose.test.yml`'s databases.
- **Logging.** The new endpoint follows `.claude/rules/logging.md`: a start line, an outcome for
  each asset, and a summary. An asset left out of the mix has to say why.
- **Docs change in the same change as the code.** That means each touched service's
  `docs/architecture.md`, plus `services/portfolio/docs/flows.md` for the new flow and
  `services/web/docs/ui-architecture.md` for the page.
- **Visual review.** The Labs page gets `review_pages.py` entries. Its screenshots are regenerated
  and looked at in the same task, at every width in the review set.
- **Files stay under 64 KB.** `services/web/src/web/api/routes.py` is close to that, so Labs gets
  its own router, as `/portfolio` and the glossary have.

## Status table

| ID | Task | Depends on | Status |
|----|------|-------------|--------|
| LB-0 | [Roadmap housekeeping](#lb-0-roadmap-housekeeping) | — | done |
| LB-1 | [data-store: a ticker's currency](#lb-1-data-store-a-tickers-currency) | — | done |
| LB-2 | [portfolio: client reads returns and currency](#lb-2-portfolio-client-reads-returns-and-currency) | LB-1 | done |
| LB-3 | [portfolio: backtest calculator](#lb-3-portfolio-backtest-calculator) | — | done |
| LB-4 | [portfolio: backtest endpoint](#lb-4-portfolio-backtest-endpoint) | LB-2, LB-3 | done |
| LB-5 | [web: client for the backtest](#lb-5-web-client-for-the-backtest) | LB-4 | done |
| LB-6 | [web: `/labs` page with the period table](#lb-6-web-labs-page-with-the-period-table) | LB-5 | done |
| LB-7 | [web: edit the mix](#lb-7-web-edit-the-mix) | LB-6 | done |
| LB-8 | [web: growth chart](#lb-8-web-growth-chart) | LB-6 | done |
| LB-9 | [portfolio: mix over the assets that cover each period](#lb-9-portfolio-mix-over-the-assets-that-cover-each-period) | — | done |
| LB-10 | [web: chart shows the mix, assets on demand](#lb-10-web-chart-shows-the-mix-assets-on-demand) | LB-7, LB-9 | done |

## Tasks

### LB-0. Roadmap housekeeping

**Status:** done

The screen-layout chunk moved to
[`docs/roadmap-archive/screen-layout.md`](screen-layout.md). Its content is
verbatim apart from relative links. This file was then started. The Deferred list is carried
over whole.

### LB-1. data-store: a ticker's currency

**Status:** done

Converting to EUR needs each asset's currency. Portfolio knows a currency only from a user's
transactions (`position.native_currency`), and a lab can include tickers the user has never
traded. data-store already stores it: `tracked_tickers.currency`, and `etf_holdings.currency`
for fund constituents. But `GET /tickers/{ticker}` (`TickerInfo`) doesn't return it.

- Add `currency` to `TickerInfo`. Take it from `tracked_tickers` when the ticker is tracked,
  otherwise from the latest `etf_holdings` snapshot that lists it, otherwise null.
- Return the provider's spelling verbatim (`GBp` stays `GBp`), as the stored columns do.
  `fx_rates` already maps a minor unit to its major currency for ticker currencies. Name that
  mapping in the docs so that LB-3 uses the same one rather than a second copy.
- A minor unit doesn't change a percentage return, because the unit cancels out of the ratio.
  It only matters for choosing the FX series.
- This changes no schema. It reads existing columns.

Tests: the read flow for a tracked ticker, a ticker known only through a fund's holdings, and a
ticker with no currency (integration). The route serializes the field (unit).

Files: `data_store/api/models.py`, `data_store/api/routes.py`, the read flow behind
`GET /tickers/{ticker}`, `services/data-store/docs/architecture.md`.

**As built:**
- **`fx_currency` as well as `currency`.** Portfolio can't import data-store's
  `ticker_onboarding.major_currency`, so naming the mapping in the docs would still have left
  LB-3 with a second copy. `TickerInfo` returns both: `currency` verbatim and
  `fx_currency = major_currency(currency)`. Consumers choose the FX pair from `fx_currency`.
- **A null tracked currency falls back to holdings.** A config row has no currency until
  onboarding fills it in.
- **Holdings are matched by resolving forward**, with ingest's rules, not by the raw symbol.
  The dev database showed iShares reusing a raw symbol across exchanges (`DG` is Vinci in Paris
  and Dollar General in New York). To share those rules, the resolution moved out of
  `pipeline.py` into `ticker_resolution.py`.
- iShares writes `GBP` where yfinance writes `GBp` for LSE stocks, so a holdings-only London
  ticker reports `GBP`. A percentage return and the FX pair are the same either way.

### LB-2. portfolio: client reads returns and currency

**Status:** done

data-store's `GET /prices/{ticker}` already returns `simple_return`.
`HttpDataStoreClient.get_price_series` drops it: `PricePoint` has only `date` and
`split_adjusted_close`.

- Add `simple_return: Decimal | None` to `PricePoint` and parse it. Keep NULL as `None`, not 0.
- Add `currency` and `fx_currency` to the client's ticker info, from LB-1. Both can be null.
- Existing callers (`returns/calculator.py`) ignore the new fields and don't change.

Tests: client parsing, including a NULL return and a missing currency.

Files: `portfolio/datastore_client/base.py`, `portfolio/datastore_client/http_client.py`, their
tests.

**As built:**
- **`simple_return`'s key is required, its value nullable.** data-store always sends the key, so
  a missing one is a `DataStoreApiError`, not a gap: defaulting it to `None` would turn a
  contract break into wrong returns. The dataclass field still defaults to `None`, so existing
  constructors (`returns/calculator.py`'s tests and fakes) didn't change.
- **A missing currency is `None`, not an error.** Absent or null, it can only leave an asset
  without an FX pair, which LB-4 reports as "no FX rate".

### LB-3. portfolio: backtest calculator

**Status:** done

A new domain module, `portfolio/labs/`, following the service's three-file shape
(`services/portfolio/docs/architecture.md`). Its core is a pure function. It takes each asset's
daily native returns, its currency's FX series, the weights, and a period. It returns each
asset's and the mix's % return for every period, and the daily growth series for one period.
It does no I/O, so it can be built before LB-2.

- **EUR conversion.** An asset's EUR return on day t is (1 + rₜ) · fxₜ₋₁ / fxₜ − 1, where fx is
  units of the currency per 1 EUR, as `fx_rates` stores it. The FX series is chosen by the
  asset's `fx_currency` from LB-1, with no minor-unit mapping of its own. An asset with no FX
  series for its currency is unavailable, with that reason. EUR assets need no FX.
- **Calendars.** Assets trade on different exchanges and holidays. Line their dates up on the
  union of trading days, and carry each asset's growth forward on days it didn't trade.
- **Gaps.** Count NULL returns inside a period and report them with the result. Before choosing
  how to bridge one, check in the dev database (read only) how often NULLs occur inside a
  history, as opposed to on its first day. Then record the choice in the module's docs.
- **Periods.** These follow the rules in Agreed requirements: end date, start dates, Max, and
  "Not enough history".
- **Buy and hold.** The mix is V(t) = Σ wᵢ · Gᵢ(t) / Gᵢ(s), re-based on each period's own start,
  so one long series can't simply be sliced into periods.

Tests: hand-computed fixtures. One asset. Two assets with drifting weights, where the answer
differs from daily rebalancing. Two calendars with different holidays. A USD asset whose price
is flat while the FX rate moves. A NULL gap. A period longer than one asset's history. Max
common history. Weights that don't sum to 1, which depends on the decision in LB-7.

Files: `portfolio/labs/` (new), its tests, `services/portfolio/docs/architecture.md` (the new
module).

**As built:**
- **Inputs are `PricePoint` and `FxPoint`.** LB-2 had merged by then, so
  `compute_backtest(assets, growth_period=...)` takes each asset's `PricePoint`s (reading only
  `simple_return`) and `FxPoint`s, rather than a look-alike type that LB-4 would copy into.
  `fx=None` means a EUR asset. An empty series, or one that starts after the asset's last
  price, makes it unavailable with "no FX rate". No prices at all is "no history yet".
- **A gap is a flat day, counted** in each period's `gaps`. The dev database, after the
  September 2026 ingest gap was repaired, has 464 NULL returns after a ticker's first day across
  9.0M rows, 446 of them single days. Blanking a period that contains one was rejected: a single
  suppressed spike would hide years of figures. The repair itself was a data-store bug fix
  (#88), not part of this roadmap.
- **FX applies every day, using the latest rate on or before it, with no age limit.** An asset's
  EUR value moves with the rate on days it didn't trade. Its usable history starts on its first
  day with a rate.
- **A period's base is each asset's value on or before its start.** Every row's Max starts on
  the latest first day among the assets. If the histories don't overlap at all, no period is
  covered.
- **Unavailable assets are left out, and the rest are re-weighted to 100%.** Rows report the
  weight actually used. Weights are normalized, per the assumption LB-7 still has to confirm.
- **The growth series** starts on the period's start date at 0, then has a point for each
  trading day of any asset. An asset that doesn't cover the period has no line, and the mix has
  none unless every asset does.

### LB-4. portfolio: backtest endpoint

**Status:** done

- **Route.** `POST /labs/backtest` with body `{assets: [{ticker, weight}], period}`. It returns:
  - the period table, with each asset and the mix in each of the seven periods,
  - the growth series for `period`, for the mix and each asset,
  - a status for each asset: included, "no history yet", "not tracked" or "no FX rate", each
    with a reason.

  It is a POST because the body is structured. It changes no state.
- **Flow.** For each asset, fetch ticker info, the price series and the FX series concurrently
  through the data-store client. Errors are isolated per asset, so one failing ticker is
  reported as unavailable rather than failing the whole call.
- **Validation.** Return a 422 for an empty mix, duplicate tickers, a weight ≤ 0, or more than a
  maximum number of assets (pick the number in this task and give the reason).
- **Logging.** A start line with the asset count, one line per asset with its outcome, and a
  summary.
- **Auth.** The same internal auth as portfolio's other routes. The mix contains no user data,
  so no `user_id` is needed.

Tests: route validation and a successful response with a fake data-store client (unit). An
asset with no history, an asset whose call fails, and an asset with no FX rate each leave the
rest intact.

Files: `portfolio/labs/`, `portfolio/api/`, `services/portfolio/docs/architecture.md` (endpoint
surface), `services/portfolio/docs/flows.md` (the new flow).

**As built:**
- **Two fetch phases, not one.** The FX pair comes from the ticker info's `fx_currency`, so
  `labs/flows.py` fetches every ticker info concurrently first. It then fetches prices, plus one
  FX series per distinct currency, concurrently too. Both phases use a pool of 8 threads,
  because the client is synchronous. Both series run from 1970-01-01, since Max needs the whole
  history.
- **A fifth status, `failed`** ("couldn't reach data-store"), for an asset whose call raised. The
  exception is logged, not returned. An outage must not look like missing data. A failed FX fetch
  fails every asset in that currency and no other.
- **`statuses` replaces the calculator's `unavailable`** in the response. It lists every
  requested asset, in order, with a code (`included`, `not_tracked`, `no_history`, `no_fx_rate`,
  `failed`) and a reason. `no_history` also covers an empty price series, and `no_fx_rate` an
  empty FX series.
- **The cap is 50 assets.** Web's default mix is the user's whole holdings, so the cap has to sit
  above any real portfolio. It still bounds a request to about 105 data-store calls. Tickers are
  limited to 20 characters, data-store's own path limit. A weight must be a positive, finite
  number.
- **`Period` became a `StrEnum`**, so the body's `period` and the response's period keys are
  `"1M"` to `"Max"` with no mapping. Figures are written in fixed-point, never `"0E-8"`.
- **Ticker quoting.** `get_ticker_info` and `get_price_series` now quote the ticker as one path
  segment. Labs is the first caller to pass them a ticker a user typed.
- portfolio's `architecture.md` was over 62 KB, so its Deployment section moved to
  `docs/deployment.md` first, in a commit of its own.

### LB-5. web: client for the backtest

**Status:** done

- Add `backtest()` to the `PortfolioClient` Protocol and to `HttpPortfolioClient`, with frozen
  result dataclasses.
- Add it to the fake clients that the web tests use.

Tests: parsing a full response, an asset that isn't included, and an error response.

Files: `web/portfolio_client/{base,http_client}.py`, their tests,
`services/web/docs/architecture.md` (the endpoints web calls).

**As built:**
- **No fake yet.** web's fake clients are partial copies, one per test file plus
  `review_pages.py`'s, and each has only the methods its routes call. No route calls `backtest()`
  until LB-6, so LB-6's new test file and `review_pages.py` add the fake with the `/labs` route.
- **A 404 is `PortfolioUnavailable`, not `AccountNotFound`.** The route has no account. A 422 is
  `InvalidPortfolioRequest`, with portfolio's message.
- **Periods are strings, with `BACKTEST_PERIODS` for the column order.** web can't import
  portfolio's `Period`. A row missing a period, or a growth line whose length differs from
  `dates`, is a malformed response, not a partial one.
- **The shared 10 s timeout fits.** LB-4's route wasn't deployed on the dev stack yet, so the
  flow was timed from the repo's code against the dev data-store. A 50-asset mix over Max took
  about 2.2 s, and its response was about 460 KB, mostly the growth series. LB-8 should weigh
  that size before putting the series in the page.
- In the dev data, `fx_rates` has no HKD, JPY, CAD, DKK, AUD or SEK series. Tickers in those
  currencies come back `no_fx_rate`, which is 15 of the 50 sampled.
- `services/web/docs/architecture.md` is now about 63.6 KB. LB-6 has to move a section out
  before adding to it.

### LB-6. web: `/labs` page with the period table

**Status:** done

The first visible page. It only reads; editing is LB-7.

- A new router, `web/api/labs.py`, a "Labs" link in `base.html`, and `templates/labs.html`.
- **Loading.** The page loads holdings for the starting mix, then the backtest. As in
  `_load_portfolio_view`, a failed holdings call shows its error without breaking the page, and
  so does a failed backtest. With no holdings at all, the page explains that there is nothing to
  backtest yet and points to adding an asset.
- **The table.**
  - The mix is the first row, and each asset follows with the weight it was given.
  - The columns are 1M to Max. "Max" shows its start date.
  - Numbers are mono, and only gains and losses use forest and oxblood.
  - Unavailable assets are listed below the table with their reason.
- **Glossary entries:** backtest, buy and hold, and the longest history all assets share. They
  are linked from the labels with the `term` macro.
- **Review.** Add `review_pages.py` entries with fake data, including an unavailable asset and
  short history. Update `ui-architecture.md`.
- **More than 50 holdings.** `POST /labs/backtest` returns a 422 for more than 50 assets (LB-4).
  A portfolio with more priced holdings than that needs the page to cut the default mix to the
  largest 50 by weight and say so, rather than show an error.

Tests: the page renders the mix row and the asset rows in order. Each failing call leaves the
other section rendered. An empty portfolio shows the explanation. Every `term` anchor exists in
the glossary. Unpriced holdings are named in the note.

Files: `web/api/labs.py`, `web/api/app.py`, `templates/{base,labs,glossary}.html`, a view builder
module, `static/web.css`, `scripts/review_pages.py`, `services/web/docs/ui-architecture.md`.

**As built:**
- **web's `architecture.md` was split first**, in a commit of its own. Its Endpoint surface
  table moved verbatim to `docs/endpoints.md`, which leaves room for LB-7 and LB-8.
- **`labs_view.build_default_mix`** reuses `build_portfolio_holdings` for the weights, so
  `/labs` and `/portfolio` can't disagree. It sends the largest first, with ties by ticker.
  Holdings left out are named in notes, one per reason: no price yet, a market value of zero
  (portfolio rejects the whole mix over one weight ≤ 0), or beyond the 50-asset cap. Past the
  cap, the weights sent are still shares of the whole portfolio, and portfolio re-normalizes
  them.
- **Holdings, then the backtest, in sequence**, each failing on its own. A 422 shows
  portfolio's message. A holdings failure runs no backtest.
- **The backtest asks for 1Y's growth series**, which the page discards until LB-8 makes the
  period a parameter. 1Y keeps the response far under Max's.
- **"Add an asset" links to Accounts** for now, where transactions are added.
  `/transactions/new` needs an account. LB-7's form becomes the real way to add an asset.
- **The gap count isn't shown.** `BacktestPeriodReturn.gaps` is parsed but no row displays
  it.
- **The table scrolls between phone width and about 1000px**, as `/portfolio`'s does, with
  "Not enough history" kept on one line. Stacked at phone width, Max's label carries its start
  date.
- The Labs nav link sits after Portfolio. `test_portfolio_page.py`'s nav-order test changed
  with it.
- `review_pages.py`'s fake backtest reports `7203.T` as `no_fx_rate`, though it was never sent,
  so that the review shows the "Not backtested" list.

### LB-7. web: edit the mix

**Status:** done

- **The form.** A GET form with one row per asset (ticker and weight %). It works without
  JavaScript: there is an empty row for adding an asset and a remove button on each row, and
  submitting reloads `/labs` with the new query string.
- **Ticker choice.** A `<datalist>` from `list_tickers()`, as on the add-transaction form.
- **Errors** appear inline beside the row:
  - an unknown ticker, with a link to "Add a ticker",
  - a duplicate,
  - a weight that isn't a positive number.

  The table keeps showing the last valid mix.
- **Weights.** Show the weights' total. Settle the normalize-or-reject assumption from Agreed
  requirements with the user before building, and update LB-3's test if it changes.
- **Reset.** A "Reset to my portfolio" link.
- Adding a row without a reload is a progressive enhancement, in a `static/` script within the
  CSP (`script-src 'self'`). It is optional for this task.

Tests: query-string parsing and round-trip, every error case, reset, and removing the last
asset.

Files: `web/api/labs.py`, `templates/labs.html`, `static/web.css`, maybe a `static/labs_form.js`,
`scripts/review_pages.py`, `services/web/docs/ui-architecture.md`.

**As built:**
- **Normalize, confirmed.** Weights are scaled to 100%, as LB-3 and portfolio already do, so
  only LB-3's test comment changed. The default mix's own rounding (99.99%) and a capped mix
  (under 100%) would have tripped a reject.
- **The query string.** It has repeated `ticker`/`weight` pairs. Any `ticker` key makes the
  mix an edited one, so an emptied mix is `?ticker=`, not the default. `remove=<n>` answers a
  303 to the mix that's left, so a reload can't remove a second row. A new pure module,
  `web/labs_mix.py`, holds the contract and the checks.
- **"The last valid mix" travels in hidden `last_ticker`/`last_weight` fields.** A form with
  errors isn't sent. The table re-runs the last mix, with a warning banner. With no last mix
  (a bookmarked URL with errors), only the form shows.
- **Tickers match in any case** and take the tracked spelling. If the ticker list can't be
  loaded, the check is skipped and portfolio's `not_tracked` status reports the ticker instead.
- **"No history yet"** needed nothing new. portfolio's `no_history` reason already reads that
  way in the "Not backtested" list.
- **The form sits below the table**, not above it, since 50 rows would push the figures a
  screen down.
- **`labs_form.js` was built.** It adds rows from a `<template>` in place.
- **The ticker list is fetched alongside the holdings** for the default mix, and alone for an
  edited one, which needs no holdings.
- **With LB-8's chart.** LB-8 merged first. The form carries the chart's `period` as a hidden
  field, and a Remove's redirect keeps it, so editing the mix doesn't reset the chart.

### LB-8. web: growth chart

**Status:** done

`portfolio_chart.js` draws one € line, with fixed element ids and no legend. Labs needs several
% lines.

- A new `static/labs_chart.js`, a multi-series chart of cumulative % growth for one period. It
  has period buttons for 1M to Max, sent as a URL parameter, because buy and hold re-bases each
  period, so the server computes the series.
- The mix's line is the main one. Each asset's line can be shown or hidden.
- **Agree the series styling against the brand charter (chapter II) before building.** The
  palette has no colours to spare: gold is the only accent, and forest and oxblood are reserved
  for gains and losses. One option is the mix in gold, with assets as stone tints or dashes and
  highlighted on hover.
- Reuse the theme redraw (`stakehold:themechange`), the CSS-variable colours and `.chart-frame`
  from `portfolio_chart.js` and `partials/_returns_chart.html`. Don't copy them.
- **Review.** The `review_pages.py` chart-height check covers the new frame.

Tests: the page renders the chart island with every series for the requested period, and the
chart's frame. An unknown period falls back to the default.

Files: `static/labs_chart.js`, `templates/labs.html`, `static/web.css`, `scripts/review_pages.py`,
`services/web/docs/ui-architecture.md`.

**As built:**
- **Styling, agreed before building.** The mix is the heavier line, colored gain/loss by segment
  like `/portfolio`'s total return. Assets are thin `--text-muted` lines, raised to `--text` while
  their box is hovered or focused. *The mix in gold was rejected:* gold on parchment is 2.2:1,
  under the 3:1 a chart's marks need. Gold stays on controls.
- **Asset lines start hidden**, so 50 lines can't bury the mix. When the mix has no line for the
  period, the covered assets start shown and a note says why. No line at all draws no chart and
  keeps the period links.
- **Period links keep the rest of the query string**, so LB-7's mix parameters will survive a
  change of period without LB-7 touching them. An unknown period falls back to 1Y and is logged.
- **`static/chart_theme.js`** now holds the theme colors and the redraw, for both charts. It went
  in first, in a commit of its own.
- **Payload.** The island's figures are floats to six significant digits, the fewest that keep
  the tooltip's hundredths of a percent exact. That is only about 30% shorter than portfolio's
  strings: a synthetic 50-asset, five-year Max island was about 645 KB. Thinning long periods to
  weekly points would be the next step if it matters.
- The gap count is still not shown.

### LB-9. portfolio: mix over the assets that cover each period

**Status:** done

On the dev stack, a portfolio holding BRK-B, which has about a month of history in
data-store, gets no mix line on the chart and no mix figure from 3M to 5Y.
`calculator.py`'s `_mix_period` gives the mix a figure only when every asset covers the
period, and the growth mix is `None` unless `len(covered) == len(series)`. This task applies
the rewritten **Short history** requirement.

- **The mix over the covered assets.** For each period, the mix uses only the assets that
  cover it, with their weights normalized to 100%. Buy and hold is unchanged:
  V(t) = Σ wᵢ′ · Gᵢ(t) / Gᵢ(s) over the covered assets. The same rule applies to the mix's
  growth series. The mix has no figure and no line only when no asset covers the period.
- **Name the assets left out.** Each of the mix's `PeriodReturn`s and the `GrowthSeries` carry
  `left_out`: the tickers left out for not covering that period, in the request's order.
  Unavailable assets are not in it, because `statuses` already reports them. The endpoint's
  response gains the field. The change is additive, so web's client keeps working until
  LB-10 reads the field.
- **Max** ~~stays the longest history all the assets share~~. *Changed when confirming it:* Max
  is each asset's whole history, and an asset joining the mix rebalances it (Agreed
  requirements, Short history).

Tests: hand-computed fixtures. A short asset left out of 1Y but included in 1M. The weights
re-normalized over the covered assets. No asset covering a period, which gives no figure.
LB-3's test for a period longer than one asset's history now expects a mix figure. The route
serializes `left_out`.

Files: `portfolio/labs/calculator.py`, `portfolio/api/` (the response model), their tests,
`services/portfolio/docs/architecture.md`, `services/portfolio/docs/flows.md`.

**As built:**
- **Stacked on LB-7's branch** (#95), which had defined this task and was still open.
- **One mix engine for both rules.** The mix runs over an "entry plan" of segments, each a date
  and the assets held from it. A month period has one segment, which is plain buy and hold.
  Max has one segment per distinct first day, and their growth is chained. The segments'
  starting values are computed once, so a 50-asset, 20-year Max with staggered starts takes
  about 0.3 s.
- **Each row's Max has its own `start`.** The mix's Max starts on the earliest first day. An
  asset whose first day falls after the common end date (the histories don't overlap) doesn't
  cover even its own Max, and the mix's Max leaves it out.
- **`left_out` is on every `PeriodReturn`**, so one shape serves both rows, but only the mix's is
  ever non-empty. It lists tickers in request order. Unavailable assets are never in it.
- **Max growth lines can hold nulls**: a late asset's line is null before its first day and 0
  on it. That isn't additive for web, whose client turned every point into a `Decimal`. So
  web's `_growth_line` and `build_growth_payload` now keep a null point, in a commit of their
  own. Chart.js draws a null as no line and leaves it out of the `index` tooltip (checked in
  the vendored build).
- **Logging.** `jobs.get_backtest` logs one line per asset the mix left out, naming the periods.
- **The glossary's Max entry** was rewritten here, since it would have been wrong the moment
  this merged. Its `longest-shared-history` anchor and label are left to LB-10.
- LB-8's "covered assets start shown" fallback can no longer fire: the mix now lacks a line only
  when no asset covers the period. web's `ui-architecture.md` says so, and LB-10 removes it.

### LB-10. web: chart shows the mix, assets on demand

**Status:** done

The user expects the chart to show the portfolio's returns, with assets as lines they choose.
LB-8 instead turns every covered asset on when the mix has no line (`labs.html`, `checked`
when `growth.mix is none`). On the dev stack that drew 11 identical muted lines with nothing
to tell them apart. The tooltip's coloured squares are Chart.js's defaults: they match no
line and aren't brand colours.

- **The client.** `portfolio_client` parses `left_out` from LB-9 on the mix's period figures
  and on the growth series. Add it to the fakes in the tests and in `review_pages.py`.
- **The table.** The mix row's cells now fill. A note under the table names, per period, the
  assets the mix left out, e.g. "From 3M to 5Y the mix leaves out BRK-B: not enough history."
- **The mix is always the main line.** Asset boxes always start unchecked, so LB-8's "covered
  assets start shown" fallback goes. A note under the chart names the assets the mix left out
  for the period shown. The "no line" note remains only for the case where no asset covers
  the period.
- **Tell asset lines apart by name, not colour.** The brand has no spare colours. Each shown
  asset line keeps its muted colour and has its ticker at its last point, drawn by a small
  Chart.js plugin inside `labs_chart.js`, with no new vendor file. Labels that would overlap
  are nudged apart. Hovering a line or its box still raises it.
- **The tooltip.** No colour squares (`displayColors: false`). The mix comes first, and only
  the shown lines are listed.
- **Max's start per row (LB-9).** Each asset's Max now runs from its own first day, and the
  mix's from the earliest asset's. The Max header's "from" date is the mix's. Show each
  asset's own start where it differs, e.g. in the cell or its `data-start`.
- **The Max glossary term.** LB-9 rewrote the definition. Its anchor
  (`longest-shared-history`) and the term's link text still say "longest shared history".
  Rename the anchor and update every `term` call with it.
- **Late Max lines.** A late asset's Max line has null points before its first day. Check that
  its end-of-line label and hover behave with them.
- **Review.** Add a `review_pages.py` variant with a short-history asset and two assets
  checked. Regenerate the screenshots at every width, in both themes.

Tests: the client parses `left_out`. The page renders the left-out notes. The boxes start
unchecked even when an asset doesn't cover the period. The "no line" note appears only when
no asset covers it.

Files: `web/portfolio_client/{base,http_client}.py`, `web/labs_view.py`, `templates/labs.html`,
`static/labs_chart.js`, `static/web.css`, `scripts/review_pages.py`, their tests,
`services/web/docs/ui-architecture.md`.

**As built:**
- **`left_out`'s key is required, its fields default to `()`.** portfolio always sends it, so a
  missing one is a malformed response, as `simple_return` was in LB-2. Reading it as "nothing
  left out" would pass the mix's figure off as every asset's. The defaults kept every existing
  fake building.
- **The table's notes are runs of periods** (`labs_view.mix_left_out_runs`). A run ends where
  the set changes, a period leaving nothing out included, so each note names exactly the
  columns it qualifies. A single period reads "Over 1Y …", as the chart's note does.
- **Max per row.** Each Max cell's `data-start` is its row's own start. An asset that starts
  after the mix also shows "from …" under its figure, which the stacked layout hides, since
  its drawn label already says it.
- **The anchor is `max-period`.** The term's link text was already "Max".
- **Line hover raises within 12px** of a shown asset line. LB-8 only raised a line from its box.
- **The date axis is capped by the plot's width.** The labels' right padding showed that
  Chart.js sizes its tick skipping against a width without the y axis. At phone width, adjacent
  dates touched. `labs_chart.js` now sets `maxTicksLimit` from the measured width.
- **The mix gets no end label.** Its weight and gain/loss color already set it apart.
- `review_pages.py`'s fake now has AAPL starting in November 2021: left out of the mix's 5Y,
  and joining its Max late. `labs-no-mix-line` became `labs-left-out`. `labs-two-lines` shows
  both Max lines, whose labels collide, and `Page.click` takes a tuple for it.

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
- Merging different listings of one security (e.g. by ISIN) into one portfolio row.
- Forms beside the list they add to (Accounts, Add transaction, Add a ticker, the admin mapping
  form), on wide screens. Left out of the screen-layout chunk by choice.

New in this chunk:

- Saved, named labs. These need a table in portfolio, so a schema to agree first.
- Rebalancing options (monthly, quarterly, yearly) beside buy and hold.
- Annualized return (CAGR) for periods of a year or more.
- Comparing an edited mix with the unedited portfolio, side by side.
- A benchmark line, such as an index or a single ETF, on the chart.
- Risk figures: volatility and maximum drawdown.
- Tagging periods with data-store's `regimes` table (e.g. the 2008 crisis).
