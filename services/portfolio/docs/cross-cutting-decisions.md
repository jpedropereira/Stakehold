# portfolio cross-cutting decisions

Companion to [`architecture.md`](architecture.md), split out of it to keep both files under the
repo's 64KB limit. That file owns the layering and the module boundaries; this one owns the
service-wide decisions, each stated as a rule with its rationale and the rejected alternative.
"Above" and "below" here refer to this file.

## Cross-cutting decisions

Each stated as a rule, with rationale and the rejected alternative, in data-store's
documented-trade-off style.

**Every transaction stores its native-currency amount exactly as the broker reported it, plus a
EUR-equivalent frozen at transaction time; point-in-time valuation additionally needs a daily FX
rate series, not one-off conversions.** The native amount is the immutable audit trail back to the
broker's own statement — anything derived from it (EUR totals, returns) must be reconstructable
and re-checkable against that source. *Rejected: storing only a EUR-normalized amount* — simpler,
but loses the ability to reconcile against what the broker actually reported, and can't
distinguish "the broker's number" from "our conversion of it" when the two are later found to
disagree. *Rejected: converting on the fly from a single current rate* — wrong for
realized-gain-at-transaction-time analysis, which needs the rate that applied when the trade
happened, not today's.

**Portfolio calls a `data-store` read API for tickers, prices and FX rates; it never reads
`data-store`'s Postgres directly, and never runs its own price/FX ingestion.** Keeps the
"each service owns its database" boundary intact and avoids two independently-drifting copies of
yfinance ticker-resolution logic `data-store` has already built and hardened. *Rejected: portfolio
reads `data-store`'s Postgres directly* — couples the two services' schemas and deployments, and
breaks the separate-database boundary the moment either schema changes. *Rejected: portfolio runs
its own FX/price ingestion* — duplicates real, non-trivial machinery (ticker resolution, exchange
suffixes, split-adjustment) that already exists and is already correct in one place.

**Cost basis is average cost (blended), not FIFO lot tracking.** Much simpler domain model — no
lot ledger, no lot-matching logic on every sale — and sufficient for the stated goal of position
value and return "at any point in time." *Rejected: FIFO lot tracking* — more precise for
tax-lot-level realized-gain reporting, but requires tracking individual purchase lots and matching
sales against them in order, a materially bigger domain model for a use case not currently
required. Notable enough to flag as a plausible v2 if tax-lot-level precision becomes a real
requirement later.

**Positions and returns are materialized — rebuilt into stored tables from the transaction log,
not computed live on every read.** Matches `data-store`'s own `returns/` shape (full rebuild per
run), and means the HTTP API's `GET /positions`/`GET /returns` (task 9) serve reads from a table,
not from replaying years of transactions on every request. *Rejected: compute on every read* —
simpler to reason about freshness, but gets slower as transaction history grows, and would have
forced the API to either replay history per request or build its own cache — reinventing
materialization anyway, just less deliberately.

**The portfolio series is summed here, on read (PF-5).** `GET /returns/portfolio` sums every
account's `return_snapshots` rows per date. Nothing is stored: it is one linear pass over rows
that are already materialized, with no history replay and no `datastore_client` call, so the cost
the decision above guards against doesn't arise. Rows are keyed on (account, ticker): keyed on
the ticker alone, as they were before PF-5, the same ticker in two accounts would collapse into
one forward-filled series, the bug UI-11 documented in web. *Why the forward-fill is in Python,
not SQL:* it needs a calendar join per (account, ticker) pair to express in SQL. *Known cost:*
the read is every return row the user has, one query per account, with no range applied, since a
pair last priced before `start` must still carry its value into the range. `start`/`end` crop
only the response.

**Growth since inception was removed.** Portfolio used to compute a time-weighted growth figure
on read (`returns/performance.py`'s `compute_growth`/`compute_ticker_growth`, served by
`GET /returns/growth` and as `growth_pct`/`by_ticker` on `GET /returns/portfolio`), shown by web
as a tile, a column and a chart view. The user didn't want it in the portfolio views:
performance analysis of that kind lives in `labs/` instead (the labs roadmap thread). With it
went the only reason for these reads to touch `transactions` and `ticker_mappings`
(`pipeline.list_trade_flows`), so the portfolio series now reads `return_snapshots` alone. The
formula and its known limits are in git history (and `docs/roadmap-archive/portfolio-view.md`).
`labs/` doesn't use it: a backtest holds a fixed mix with no cash flows in or out, so there is
nothing to time-weight.

**Holdings across accounts are summed here, on read (PF-1).** `GET /holdings` sums each user's
latest position and return rows per ticker. Nothing is stored: it reads the same latest rows
`GET /positions` and `GET /returns/latest` serve, one pair of queries per account, so there is no
history to replay. *Rejected: summing in `web`* — web would make two calls per account, and
PF-5's cross-account return series needs a server-side path anyway, since fetching every
account's full history into web is what UI-11 avoided as too expensive. The rules:

- **A row is a ticker with an open position (`quantity != 0`) in at least one account.** Only
  those positions feed it. A negative quantity is kept, as web's `filter_open_positions` keeps
  it: it is a replay anomaly to show, not hide.
- **Units and cost come from positions, values from return rows**, as web's per-account page
  joins them. Percentages are recomputed from summed euros, never averaged.
- **No partial sums.** If any contributing account has no return row for the ticker yet, the
  row's value figures are null and it reads `priced = false`; the per-account breakdown still
  shows what is priced.
- **Native figures only when the currencies agree.** Otherwise they are null and the row reads
  `mixed_currency = true`. EUR figures are always summed.
- **The totals follow the home page's rule**, not the rows'. web's `summarize_accounts` sums every
  latest return row, closed tickers included, because a closed position's dividends are still
  part of the return. Totals built from the open rows would leave them out and disagree with the
  home page, so the response carries a separate `totals` object worked out that way.

*Known limits:* holdings merge only when they resolve to the same `canonical_ticker`, so two
listings of one fund (`IWDA.AS`, `SWDA.L`) stay separate rows. The read is 2N queries for N
accounts; a single query joining through `accounts` is the fix if that ever matters, as long as
it still starts from `accounts.user_id`.

**CLI first; orchestration is plain callables so the HTTP API added a caller, not a rewrite.**
Mirrors data-store's `jobs.py` seam — `cli.py`/`api/routes.py` are both argument parsing plus one
call into `jobs.run_*`/`jobs.get_*`. *Rejected: build the domain against a web framework's
handlers from day one* — would have coupled domain logic to HTTP concerns (request parsing,
status codes) years before task 9 needed them, the same coupling `jobs.py` exists to prevent
between data-store's CLI and Airflow. Confirmed rather than merely assumed at task 9: the API
route bodies really did end up as one call into an existing `jobs.py` function each, with no
domain logic duplicated.

**Every account belongs to exactly one user, and `accounts/repository.py` is the single chokepoint
every other domain module's queries pass through to get there.** Isolation is enforced in one
place, not asserted independently by each module. *Rejected: scope every table's queries by
`user_id` directly, module by module* — correct in principle, but repeats the same check
everywhere instead of making a cross-user leak structurally hard to introduce by accident.

**An unmapped ticker link blocks that instrument's calculation, loudly — never a silent skip.**
Directly required by the domain: silently omitting an unmapped position would understate the
portfolio without any visible sign of why. Structurally this is the same "nothing to do vs.
something went wrong" distinction data-store's `run_result` already encodes (`partial`, not `ok`,
when something is skipped) — portfolio's rebuild flows reuse that contract rather than inventing a
new one.

**Logging follows the same convention data-store settled on**: `logger.info`/`warning` to stderr
via a `configure_logging()` call at every entry point, a command's actual result to stdout — since
portfolio is a CLI service in the same shape, and the reasoning ("logs go to stderr, results go to
stdout" so output stays pipeable and ordered) applies unchanged.

**Ticker mappings live in the database (`ticker_mappings`), managed by an admin.** One global
table, written from `web`'s admin page (or the `ticker-mapping` CLI, for when `web` is down), read
once per rebuild into the same in-memory `TickerMappings` lookup the rest of the pipeline already
used. Saving or deleting one rebuilds the affected accounts' positions (see "Positions follow every
ticker-mapping change").

*Why it moved:* for most of the project this was a hand-edited YAML file, chosen to defer a CRUD
investment until a UI existed. The file was baked into the image at build time, so every mapping
fix meant editing a file on the host and rebuilding a container, and a new user's imported
instrument stayed blocked until the operator did that. Once `web` had an admin role, the file was
the bottleneck.

*Global, not per user,* as the YAML always was and `TickerLink` was always designed: an ISIN or a
broker's symbol names the same instrument whoever holds it, so a per-user table would make every
user map the same ETF separately. The cost is that changing a mapping needs a role above "user" —
the admin role (`auth`'s `users.is_admin`). **The admin check lives only in `web`**
(`require_admin`); portfolio's mapping endpoints trust their internal-secret caller like every
other endpoint here, since a check in portfolio would rest on the same trust (a forwarded flag)
and add nothing. `updated_by` records which admin last set a mapping.

*Rejected: keep the YAML and add a mount* — fixes the rebuild-to-edit problem but not the
operator-only one, and gives no way to see what's waiting to be mapped. *Rejected: a mappings
table per user* — see above. *Rejected: importing the YAML file on first `db init`* — one-off code
for a handful of entries in the only deployment; the admin page lists exactly what needs mapping
instead.
