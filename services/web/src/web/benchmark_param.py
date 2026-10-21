"""The `?benchmark=` field on /labs and /risk (RK-14): what to send portfolio for it.

Pure, like labs_mix.py. Nothing is stored (a saved choice needs a settings schema, deferred), so
the query string is the whole state, and no field means portfolio's default.

Only what portfolio would reject is caught here: a ticker over its 20-character limit, which
would otherwise make the whole backtest a 422. A ticker portfolio doesn't track is still sent:
portfolio answers 200 and greys every relative figure, "benchmark unavailable: not tracked", so
the page says why without web keeping a second copy of the tracked list.
"""

from web.portfolio_client.base import DEFAULT_BENCHMARK

# portfolio's BenchmarkTicker limit, data-store's own on a ticker (RK-13). web can't import it.
MAX_TICKER_LENGTH = 20


def chosen_benchmark(raw: str | None) -> tuple[str, str | None]:
    """The ticker to send, trimmed but in the case it was typed (yfinance tickers are
    case-sensitive), and None; or the default and the error to show beside the field."""
    ticker = (raw or "").strip()
    if not ticker:
        return DEFAULT_BENCHMARK, None
    if len(ticker) > MAX_TICKER_LENGTH:
        return DEFAULT_BENCHMARK, (
            f"A ticker is at most {MAX_TICKER_LENGTH} characters, so {DEFAULT_BENCHMARK} is shown"
            " instead."
        )
    return ticker, None
