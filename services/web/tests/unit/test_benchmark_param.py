"""benchmark_param.chosen_benchmark (RK-14): what a page's `?benchmark=` sends to portfolio, and
the error it shows instead when the field can't be sent."""

from web.benchmark_param import chosen_benchmark
from web.portfolio_client.base import DEFAULT_BENCHMARK


def test_no_field_is_the_default():
    assert chosen_benchmark(None) == (DEFAULT_BENCHMARK, None)


def test_a_blank_field_is_the_default():
    assert chosen_benchmark("   ") == (DEFAULT_BENCHMARK, None)


def test_a_ticker_is_sent_trimmed():
    assert chosen_benchmark("  IWDA.AS ") == ("IWDA.AS", None)


def test_the_case_is_kept_as_typed():
    # yfinance tickers are case-sensitive; portfolio says whether it tracks this spelling.
    assert chosen_benchmark("iwda.as") == ("iwda.as", None)


def test_an_untracked_ticker_is_still_sent():
    # portfolio answers it with "benchmark unavailable: not tracked", which the page shows.
    assert chosen_benchmark("NOPE.XX") == ("NOPE.XX", None)


def test_twenty_characters_is_sent():
    assert chosen_benchmark("A" * 20) == ("A" * 20, None)


def test_over_twenty_characters_falls_back_to_the_default_with_an_error():
    ticker, error = chosen_benchmark("A" * 21)

    assert ticker == DEFAULT_BENCHMARK
    assert error == f"A ticker is at most 20 characters, so {DEFAULT_BENCHMARK} is shown instead."
