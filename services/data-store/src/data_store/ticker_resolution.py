"""Turning a raw scraped holding (ticker + exchange) into the ticker the provider prices it under.

Its own module, not part of `pipeline.py`, because two sides need the exact same answer: market
data ingest and backfill (what to fetch), and the read API (which `etf_holdings` row a priced
ticker came from, for its currency). A second copy of these rules would drift, and a holding the
two sides resolved differently would get another company's currency.

Nothing here logs. A holding on an unmapped exchange is worth a warning during ingest -- it's a
ticker that won't be priced -- but the read API resolves every holding on every call, where the
same warning would just be noise. So the caller decides.
"""

_PLACEHOLDER_SYMBOLS = {"", "-"}


# Per-exchange raw-ticker normalization applied before appending the exchange suffix.
# yfinance's ticker syntax for a market doesn't always match the raw symbol iShares scrapes --
# these are the specific, verified-against-real-yfinance mismatches found so far. Scoped by
# exact exchange name (not applied globally) so an exchange with no known quirk is never
# touched.
def _zero_pad_hk(raw_ticker: str) -> str:
    return raw_ticker.zfill(4)


def _normalize_london(raw_ticker: str) -> str:
    # London raw tickers use "." two ways: a trailing full-stop with no meaning of its own
    # (e.g. "BP.", "NG.") -- stripped, or it doubles the "." already in the ".L" suffix -- and
    # a mid-string class-share separator (e.g. "BT.A") -- which yfinance expects as "-", same
    # as the Nordic/Toronto class-share convention. Stripping the trailing dot first means any
    # dot left over is necessarily the class-share kind.
    return raw_ticker.rstrip(".").replace(".", "-")


def _space_to_hyphen(raw_ticker: str) -> str:
    return raw_ticker.replace(" ", "-")


def _dot_to_hyphen(raw_ticker: str) -> str:
    return raw_ticker.replace(".", "-")


TICKER_NORMALIZERS = {
    "Hong Kong Exchanges And Clearing Ltd": _zero_pad_hk,
    "London Stock Exchange": _normalize_london,
    "Nasdaq Omx Nordic": _space_to_hyphen,
    "Nasdaq Omx Helsinki Ltd.": _space_to_hyphen,
    "Omx Nordic Exchange Copenhagen A/S": _space_to_hyphen,
    "Oslo Bors Asa": _space_to_hyphen,
    "Toronto Stock Exchange": _dot_to_hyphen,
}


def holding_symbol(raw_ticker: str | None) -> str | None:
    """The raw ticker, stripped, or None for a line with no real symbol. "-" is how the source
    spells "this line has no symbol" (cash, some derivative legs). It survives an emptiness
    check, and on a mapped exchange it would become a real request like "-.L"."""
    ticker = (raw_ticker or "").strip()
    return None if ticker in _PLACEHOLDER_SYMBOLS else ticker


def resolve_yfinance_ticker(
    ticker: str,
    exchange: str | None,
    exchange_suffixes: dict[str, str | None],
    ticker_overrides: dict[str, str] | None = None,
) -> str | None:
    """`ticker` + the yfinance suffix mapped for `exchange`, with any exchange-specific
    normalization applied first (e.g. zero-padding Hong Kong codes, hyphenating Nordic
    share-class tickers -- see TICKER_NORMALIZERS). `ticker_overrides`, when it has an entry
    for `ticker`, wins outright and skips exchange resolution entirely -- for one-off cases a
    general rule can't cover: a raw ticker needing a full rewrite, or one whose source exchange
    tagging is simply wrong. Returns None when `exchange` has no entry in exchange_suffixes and
    no override applies -- an unresolvable ticker must never be sent to yfinance bare, since
    yfinance won't error on a wrong/bare ticker, it just silently returns no data."""
    if ticker_overrides and ticker in ticker_overrides:
        return ticker_overrides[ticker]

    exchange_key = (exchange or "").strip()
    if exchange_key not in exchange_suffixes:
        return None

    normalizer = TICKER_NORMALIZERS.get(exchange_key)
    if normalizer:
        ticker = normalizer(ticker)

    suffix = exchange_suffixes[exchange_key] or ""
    return f"{ticker}{suffix}"
