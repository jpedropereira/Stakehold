# portfolio broker-import extension points

Companion to [`architecture.md`](architecture.md), split out of it to keep both files under the
repo's 64KB limit. It covers the two plugin surfaces broker parsers implement. "Above" and
"below" refer to this file; other sections are in `architecture.md` or
[`flows.md`](flows.md), named as such.

## Extension points: the broker-import registries

Same pattern as data-store's `SCRAPERS` / `MARKET_DATA_SOURCES` — a `Protocol` plus a registry
dict, no branching in flow functions — but populated by `broker_import/discovery.py` reading
entry points rather than hardcoded in `pipeline.py`. Two independent Protocol/registry pairs, not
one: `BrokerParser`/`BROKER_PARSERS` (plain export files, read by `import_transactions`) and
`CashflowParser`/`CASHFLOW_PARSERS` (cash-ledger formats needing a live FX lookup mid-parse, read
by `import_cashflow`). A broker key can appear in one registry, the other, both, or neither,
independently — one broker key is in both today, since that plugin implements both Protocols;
nothing enforces they agree.

```python
# broker_import/base.py  (built as of task 6; CashflowParser added during extraction prep)
class BrokerParser(Protocol):
    broker_key: str  # the registry key (e.g. "acme"). accounts.broker is the persisted
                      # source of truth for which broker an account belongs to; nothing on
                      # CanonicalTransaction repeats it (see below).

    def parse(self, file_path: Path, account_id: int) -> list["CanonicalTransaction"]:
        """Parse one broker export file into normalized transactions for one account.

        account_id is the resolved accounts.id (BIGINT), supplied by the caller after
        resolving --account through accounts/repository.py's get_by_label -- not discovered
        from the file itself. Neither known export format self-identifies its account.
        """
        ...


ResolveFxRate = Callable[[str, date], Decimal | None]


class CashflowParser(Protocol):
    broker_key: str  # same role as BrokerParser.broker_key, in the separate CASHFLOW_PARSERS
                      # registry.

    def parse(
        self, file_path: Path, account_id: int, resolve_fx_rate: ResolveFxRate
    ) -> list["CanonicalTransaction"]:
        """Parse one cash-ledger export. Unlike BrokerParser.parse, carries resolve_fx_rate
        explicitly -- only for formats whose rows have no EUR-equivalent at all."""
        ...
```

```python
# broker_import/discovery.py
BROKER_PARSER_GROUP = "stakehold.broker_parsers"
CASHFLOW_PARSER_GROUP = "stakehold.cashflow_parsers"


def discover_broker_parsers() -> dict[str, "BrokerParser"]:
    return _discover(BROKER_PARSER_GROUP)


def discover_cashflow_parsers() -> dict[str, "CashflowParser"]:
    return _discover(CASHFLOW_PARSER_GROUP)
```

`_discover` instantiates every class registered under the group (`entry_points(group=...)`),
raising `BrokerPluginError` — fail-fast, matching this file's "unrecognized type is a parse
error" ethos — on either two installed plugins claiming the same key within one group, or a
loaded parser's own `broker_key` disagreeing with the name it registered under. Editing a
`[project.entry-points]` table does nothing until the owning package is reinstalled
(`entry_points()` reads installed `.dist-info/entry_points.txt`, not `pyproject.toml` live) —
worth remembering before assuming a registration change took effect.

`broker_import/base.py` and the two Protocols it defines are built as of task 6 -- what follows
here records the design-level facts; each installed plugin's own module docs cover its exact
parsing rules, several of which corrected assumptions an earlier pass of this document made
before real export files were available to check them against.

The canonical intermediate representation every plugin's very different raw export shape gets
parsed into -- **built as of task 5** (`transactions/base.py`), so this is the real shape, not a
proposal:

```python
# transactions/base.py
@dataclass(frozen=True)
class CanonicalTransaction:
    account_id: int                                 # the resolved accounts.id, not a label
    broker_transaction_ref: str                      # natural id if the broker gives one,
                                                       # else a stable content hash (see risk below)
    transaction_type: str                             # BUY | SELL | DEPOSIT | WITHDRAWAL |
                                                       # DIVIDEND | FX | FEE | ADJUSTMENT
    trade_datetime: datetime
    instrument_identifier: str | None                 # ISIN or symbol, whichever the broker
                                                       # reports; None for cash-only rows
                                                       # (deposits, adjustments)
    instrument_identifier_kind: str | None             # "ISIN" | "SYMBOL"
    quantity: Decimal | None                          # signed: +buy, -sell
    price: Decimal | None                             # per unit, native currency
    native_currency: str                               # ISO 4217, as the broker reports it
    native_amount: Decimal                             # signed cash-flow, broker's own figure --
                                                        # the source of truth
    eur_amount: Decimal                                # signed, frozen at transaction time
    fx_rate: Decimal | None                            # required whenever native_currency != EUR
    fx_rate_source: str | None                          # "broker_reported" | "companion_row" |
                                                        # "datastore_series" | "manual"
    fees_native: Decimal | None
```

**Two corrections against the version of this shape drafted before task 5 was built.** No
`broker` field: `schema.md` dropped the functionally-dependent `transactions.broker` *column* at
sign-off (task 4), and keeping it on the in-memory record would let a producer disagree with its
own account about who the broker is for no benefit — a parser fills `CanonicalTransaction` without
it, and `broker_key` above stays purely a registry key. `account_id` is `int`
(`accounts.id`, a `BIGSERIAL`), not `str` — resolving a caller-supplied label to that id, scoped by
`user_id`, is `accounts/repository.py`'s job, done before a `CanonicalTransaction` is ever built.
`transactions/base.py`'s `validate()` enforces the field-level rules above, including that a
non-EUR row must carry an `fx_rate` — a producer (manual entry, or a future broker parser) that
skips resolving one is rejected, not persisted as a gap to patch up later.

Registries, populated once at import time:

```python
# pipeline.py
BROKER_PARSERS: dict[str, BrokerParser] = discover_broker_parsers()
CASHFLOW_PARSERS: dict[str, CashflowParser] = discover_cashflow_parsers()
```

Today's two `BROKER_PARSERS` entries and one `CASHFLOW_PARSERS` entry all resolve to
implementations installed from a separate, privately-maintained plugin package — see the
"Revised again — broker code extracted" note in [`architecture.md`](architecture.md). Nothing in this repo names that package or
depends on it; it's discovered purely through the two entry-point groups above, at whatever
process picks up its installed metadata.

**`BROKER_PARSERS` is read in exactly one place outside its own definition: `import_transactions`**
(via `BROKER_PARSERS.get(broker)`, raising `BrokerParserNotFound` when nothing is registered for
the given key). **`CASHFLOW_PARSERS` is read the same way, exactly once, by `import_cashflow`.**
`add_account` deliberately reads neither — see the "Revised after task 6" note in `architecture.md` for why
account creation and manual entry must work with both registries empty.
`pipeline.list_available_brokers()` (`portfolio broker list`, `GET /brokers`) reads both
registries, read-only, and returns one `AvailableBroker` per broker key found in either, with the
import kinds it supports: `"transactions"` (in `BROKER_PARSERS`) and/or `"cashflow"` (in
`CASHFLOW_PARSERS`). This is what `web`'s import page offers as its importer choice. Before
that page existed, it reported `BROKER_PARSERS` keys alone, so a broker with only a cashflow
parser (one exists today) was invisible to every caller and could only be imported through the
CLI. Because it reads both registries, a stashed discovery error from either one is raised here,
rather than that registry's brokers silently showing fewer imports than they have.

**Worked example — adding a third broker** (e.g. Initech): implement `BrokerParser` and
register it under the `stakehold.broker_parsers` entry-point group as `initech = "...:
InitechParser"` in whatever package provides it (this one, today; a separate installable
package, once the extraction below completes) — no change to `pipeline.py`'s flow function,
`jobs.py`, or `cli.py`'s argument shape. `import-transactions --file ... --account ...` works
against a Initech account as soon as that package is installed and its metadata is picked up
(`entry_points()`, at next process start) — the account's own `broker` column, not a CLI flag,
is what selects `initech` from the registry.

**A plugin's own parsing rules — locale formats, id-collision handling, per-broker FX
conventions — are documented in that plugin package's own docs, not here.** This document owns
the shape of the extension point (the Protocols, the registries, the flow functions), not any
particular implementation. (The two plugins this deployment actually installs, and the real
production risks their authors found — a non-unique natural key, an undocumented base-currency
convention — are written up in that private package's own `docs/architecture.md`, in the same
verified-against-a-real-export style this document uses elsewhere.)

**Disclosed trade-off, matching data-store's style**: `parse(file_path, account_id)` assumes a
broker's history always arrives as one downloadable file. A future broker exposing only a
paginated API, not a file export, would have to be adapted into "write it to a temp file, then
parse it," or the Protocol would need broadening. Accepted for now, same reasoning data-store used
for its single `MarketDataSource` Protocol: both known brokers are file-export-based, and there's
nothing to generalize from yet.

**A real natural key can turn out not to be unique, and a real installed plugin found exactly
that case.** `broker_transaction_ref`'s uniqueness (with `account_id`) is the whole idempotent-
reimport contract, so a parser can't simply trust whatever id field a broker export happens to
provide — one real broker's plugin found its export's obvious-looking id column collides on a
partial fill, and had to fall back to a content hash plus an occurrence index instead. Written up
in that plugin's own docs, not here, since the specifics (which broker, which column) are exactly
what stays out of this repo.
