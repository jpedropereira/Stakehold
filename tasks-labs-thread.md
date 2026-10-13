# Tasks

Roadmap for the labs chunk: a new top-level section, **Labs**, for backtesting. It shows how the
user's current portfolio would have done over the last month, 3 months, 6 months, year, 2 years,
5 years and the longest history all its assets share. It shows this for the mix as a whole and for
each asset. The user can add or remove assets and change their weights to see how that changes
returns. Only percentage returns matter here. The portfolio's value plays no part.
Commit a726e51 removed "growth since inception" from the portfolio views so that this kind of
analysis could live here instead.
The previous chunks are archived in [`docs/roadmap-archive/`](docs/roadmap-archive/):
[walking skeleton](docs/roadmap-archive/walking-skeleton.md),
[UI/UX](docs/roadmap-archive/ui-ux.md),
[ticker onboarding](docs/roadmap-archive/ticker-onboarding.md),
[portfolio view](docs/roadmap-archive/portfolio-view.md) and
[screen layout](docs/roadmap-archive/screen-layout.md).

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
  - **Max** starts at the latest first date among the mix's assets, which is the longest history
    they all share.
  - **Short history.** A period longer than an asset's history shows "Not enough history" for
    that asset. The mix's figure for that period is shown only when every asset covers it.
- **Buy and hold.** The mix is bought at its weights on the period's first day. Weights then
  drift with prices, with no rebalancing. Each period starts again from the target weights on its
  own first day. With Gᵢ(t) as asset i's growth since the start date s, the mix grows as
  V(t) = Σ wᵢ · Gᵢ(t) / Gᵢ(s).
- **Total return in EUR.** Each asset's daily return includes dividends: data-store's
  `market_returns.simple_return`, chained. It is converted to EUR through `fx_rates`, so a USD
  asset's return includes the USD/EUR move. Returns are percentages only.
- **Editing.** The user can add any ticker that data-store has history for (the
  `GET /instruments/tickers` list), remove assets, and change weights. Weights are entered as
  percentages. A ticker that isn't tracked links to "Add a ticker". A ticker that is tracked but
  has no returns yet (still onboarding) shows "No history yet" and is left out of the mix.
- **Weights that don't add up to 100%.** *Assumption, to confirm in LB-7:* they are normalized,
  and the page shows the weights it actually used. The alternative is to reject the mix with a
  form error.
- **Not saved.** The mix is held in the query string, so a lab can be bookmarked or reloaded, but
  nothing is stored. "Reset to my portfolio" is `/labs` with no parameters. Saving labs is
  deferred.
- **Outputs.** There are two:
  - A table: a row for the mix and a row for each asset, with the seven periods as columns.
  - A growth chart: the mix's cumulative % growth over a chosen period, with each asset's growth
    available as a line.

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
| LB-4 | [portfolio: backtest endpoint](#lb-4-portfolio-backtest-endpoint) | LB-2, LB-3 | ready |
| LB-5 | [web: client for the backtest](#lb-5-web-client-for-the-backtest) | LB-4 | blocked |
| LB-6 | [web: `/labs` page with the period table](#lb-6-web-labs-page-with-the-period-table) | LB-5 | blocked |
| LB-7 | [web: edit the mix](#lb-7-web-edit-the-mix) | LB-6 | blocked |
| LB-8 | [web: growth chart](#lb-8-web-growth-chart) | LB-6 | blocked |

## Tasks

### LB-0. Roadmap housekeeping

**Status:** done

The screen-layout chunk moved to
[`docs/roadmap-archive/screen-layout.md`](docs/roadmap-archive/screen-layout.md). Its content is
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

**Status:** ready

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

### LB-5. web: client for the backtest

**Status:** blocked

- Add `backtest()` to the `PortfolioClient` Protocol and to `HttpPortfolioClient`, with frozen
  result dataclasses.
- Add it to the fake clients that the web tests use.

Tests: parsing a full response, an asset that isn't included, and an error response.

Files: `web/portfolio_client/{base,http_client}.py`, their tests,
`services/web/docs/architecture.md` (the endpoints web calls).

### LB-6. web: `/labs` page with the period table

**Status:** blocked

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

Tests: the page renders the mix row and the asset rows in order. Each failing call leaves the
other section rendered. An empty portfolio shows the explanation. Every `term` anchor exists in
the glossary. Unpriced holdings are named in the note.

Files: `web/api/labs.py`, `web/api/app.py`, `templates/{base,labs,glossary}.html`, a view builder
module, `static/web.css`, `scripts/review_pages.py`, `services/web/docs/ui-architecture.md`.

### LB-7. web: edit the mix

**Status:** blocked

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

### LB-8. web: growth chart

**Status:** blocked

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
