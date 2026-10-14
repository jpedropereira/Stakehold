"""labs_mix (LB-7): the mix /labs backtests when the user has edited it -- reading it from the
query string, checking each row, and writing it back."""

from decimal import Decimal
from urllib.parse import parse_qsl

import pytest
from web.labs_mix import (
    MAX_BACKTEST_ASSETS,
    MixRow,
    canonical_query,
    check_mix,
    default_rows,
    parse_mix,
)
from web.labs_view import DefaultMix

TICKERS = ["VWCE.DE", "AAPL", "IWDA.AS"]


def _params(query):
    return parse_qsl(query, keep_blank_values=True)


def _rows(*pairs):
    return tuple(MixRow(ticker, weight) for ticker, weight in pairs)


# --- parsing ----------------------------------------------------------------------------------


def test_no_ticker_parameter_is_the_default_mix():
    assert parse_mix(_params("")) is None
    assert parse_mix(_params("theme=dark")) is None


def test_rows_pair_tickers_and_weights_in_order():
    form = parse_mix(_params("ticker=VWCE.DE&weight=60&ticker=AAPL&weight=40"))

    assert form.rows == _rows(("VWCE.DE", "60"), ("AAPL", "40"))
    assert form.remove is None
    assert form.last == ()


def test_blank_rows_are_dropped_and_values_trimmed():
    form = parse_mix(_params("ticker=+AAPL+&weight=+40+&ticker=&weight=&ticker=VWCE.DE&weight=60"))

    assert form.rows == _rows(("AAPL", "40"), ("VWCE.DE", "60"))


def test_a_ticker_with_no_weight_or_a_weight_with_no_ticker_is_kept_as_a_row():
    form = parse_mix(_params("ticker=AAPL&weight=&ticker=&weight=20"))

    assert form.rows == _rows(("AAPL", ""), ("", "20"))


def test_unequal_counts_pad_the_shorter_list():
    form = parse_mix(_params("ticker=AAPL&ticker=VWCE.DE&weight=40"))

    assert form.rows == _rows(("AAPL", "40"), ("VWCE.DE", ""))


def test_an_edited_mix_can_be_empty():
    form = parse_mix(_params("ticker="))

    assert form is not None
    assert form.rows == ()


def test_remove_drops_that_row_by_its_position_in_the_form():
    form = parse_mix(
        _params("ticker=AAPL&weight=40&ticker=&weight=&ticker=VWCE.DE&weight=60&remove=2")
    )

    assert form.remove is not None
    assert form.without_removed() == _rows(("AAPL", "40"))


@pytest.mark.parametrize("remove", ["9", "-1", "x", ""])
def test_a_remove_that_names_no_row_removes_nothing(remove):
    form = parse_mix(_params(f"ticker=AAPL&weight=40&remove={remove}"))

    assert form.without_removed() == _rows(("AAPL", "40"))


def test_the_last_mix_shown_is_read_from_its_own_fields():
    form = parse_mix(
        _params(
            "ticker=AAPX&weight=40"
            "&last_ticker=AAPL&last_weight=40&last_ticker=VWCE.DE&last_weight=60"
        )
    )

    assert form.last == _rows(("AAPL", "40"), ("VWCE.DE", "60"))


# --- writing it back --------------------------------------------------------------------------


def test_canonical_query_round_trips():
    rows = _rows(("VWCE.DE", "60"), ("AAPL", "40.5"))
    last = _rows(("AAPL", "100"))

    form = parse_mix(_params(canonical_query(rows, last)))

    assert form.rows == rows
    assert form.last == last
    assert form.remove is None


def test_canonical_query_of_an_empty_mix_still_marks_it_edited():
    query = canonical_query((), ())

    assert query == "ticker="
    assert parse_mix(_params(query)).rows == ()


def test_canonical_query_escapes_what_a_ticker_can_contain():
    rows = _rows(("^GSPC", "50"), ("EURUSD=X", "50"))

    assert parse_mix(_params(canonical_query(rows, ()))).rows == rows


def test_default_rows_are_the_mix_weights_as_percentages():
    mix = DefaultMix(
        assets=(("IWDA.AS", Decimal("0.75")), ("AAPL", Decimal("0.33333"))),
        unpriced=(),
        unweighted=(),
        dropped=(),
    )

    assert default_rows(mix) == _rows(("IWDA.AS", "75"), ("AAPL", "33.33"))


# --- checking ---------------------------------------------------------------------------------


def test_a_valid_mix_is_sent_as_fractions_with_its_total():
    checked = check_mix(_rows(("VWCE.DE", "60"), ("AAPL", "40")), TICKERS)

    assert checked.valid
    assert checked.assets == (("VWCE.DE", Decimal("0.6")), ("AAPL", Decimal("0.4")))
    assert checked.total == Decimal("1")
    assert all(not row.errors for row in checked.rows)


def test_weights_that_do_not_add_up_to_100_are_still_valid():
    # Agreed in LB-7: portfolio normalizes them; the page shows the total and the weights used.
    checked = check_mix(_rows(("VWCE.DE", "60"), ("AAPL", "60")), TICKERS)

    assert checked.valid
    assert checked.total == Decimal("1.2")


def test_a_percent_sign_and_a_decimal_comma_are_accepted():
    checked = check_mix(_rows(("VWCE.DE", "60,5%"), ("AAPL", "39.5 %")), TICKERS)

    assert checked.assets == (("VWCE.DE", Decimal("0.605")), ("AAPL", Decimal("0.395")))


def test_an_unknown_ticker_is_marked_with_a_link_to_add_it():
    checked = check_mix(_rows(("VWCE.DE", "60"), ("NOPE", "40")), TICKERS)

    assert not checked.valid
    assert checked.assets == ()
    assert checked.rows[0].errors == ()
    assert checked.rows[1].unknown_ticker
    assert checked.rows[1].errors == ("Stakehold doesn't track this ticker.",)


def test_a_ticker_in_another_case_takes_the_tracked_spelling():
    checked = check_mix(
        _rows(
            ("vwce.de", "100"),
        ),
        TICKERS,
    )

    assert checked.valid
    assert checked.rows[0].ticker == "VWCE.DE"
    assert checked.assets == (("VWCE.DE", Decimal("1")),)


def test_without_a_ticker_list_any_ticker_is_sent():
    # The list couldn't be loaded; portfolio still says which tickers it doesn't track.
    checked = check_mix(
        _rows(
            ("NOPE", "100"),
        ),
        None,
    )

    assert checked.valid
    assert checked.assets == (("NOPE", Decimal("1")),)


def test_a_duplicate_is_marked_on_every_repeat_but_the_first():
    checked = check_mix(_rows(("AAPL", "40"), ("VWCE.DE", "20"), ("aapl", "40")), TICKERS)

    assert not checked.valid
    assert checked.rows[0].errors == ()
    assert checked.rows[2].errors == ("Already in the mix above.",)


@pytest.mark.parametrize("weight", ["0", "-5", "abc", "inf", "nan", "NaN", "1_0x"])
def test_a_weight_that_isnt_a_positive_number_is_marked(weight):
    checked = check_mix(
        _rows(
            ("AAPL", weight),
        ),
        TICKERS,
    )

    assert not checked.valid
    assert checked.rows[0].errors == ("Enter a weight above 0%.",)


def test_a_missing_weight_or_ticker_is_marked():
    checked = check_mix(_rows(("AAPL", ""), ("", "20")), TICKERS)

    assert checked.rows[0].errors == ("Enter a weight.",)
    assert checked.rows[1].errors == ("Enter a ticker.",)


def test_a_row_can_have_a_ticker_error_and_a_weight_error():
    checked = check_mix(
        _rows(
            ("NOPE", "0"),
        ),
        TICKERS,
    )

    assert checked.rows[0].errors == (
        "Stakehold doesn't track this ticker.",
        "Enter a weight above 0%.",
    )


def test_the_total_counts_only_the_weights_that_are_numbers():
    checked = check_mix(_rows(("AAPL", "40"), ("VWCE.DE", "abc"), ("IWDA.AS", "20")), TICKERS)

    assert checked.total == Decimal("0.6")


def test_more_rows_than_the_backtest_takes_is_an_error_for_the_mix():
    tickers = [f"T{i:02d}" for i in range(MAX_BACKTEST_ASSETS + 1)]

    checked = check_mix(_rows(*((t, "1") for t in tickers)), tickers)

    assert not checked.valid
    assert checked.error == f"A mix can have at most {MAX_BACKTEST_ASSETS} assets."
    assert all(not row.errors for row in checked.rows)


def test_an_empty_mix_is_valid_with_nothing_to_send():
    checked = check_mix((), TICKERS)

    assert checked.valid
    assert checked.assets == ()
    assert checked.total == Decimal("0")
