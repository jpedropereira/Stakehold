"""saved_labs_view.py: a saved lab's open URL, which saved lab the page is showing, the summary a
delete confirms, and the status parameters every link leaves behind."""

from urllib.parse import parse_qsl

from web.labs_mix import MixRow, parse_mix
from web.portfolio_client.base import SavedLab
from web.saved_labs_view import (
    back_query,
    matching_lab,
    mix_summary,
    open_url,
    status_message,
    view_params,
)

CORE = SavedLab(name="Core", assets=(("VWCE.DE", "60"), ("AAPL", "40")), updated_at="2026-11-02")
TECH = SavedLab(name="Tech", assets=(("MSFT", "100"),), updated_at="2026-11-01")


def test_open_url_is_the_mix_as_the_form_submits_it():
    url = open_url(CORE)

    assert url.startswith("/labs?")
    params = parse_qsl(url.removeprefix("/labs?"))
    form = parse_mix(params)
    assert form.rows == (MixRow("VWCE.DE", "60"), MixRow("AAPL", "40"))
    assert form.last == ()


def test_matching_lab_finds_the_lab_with_the_same_tickers_and_weights_in_order():
    shown = (MixRow("VWCE.DE", "60.00"), MixRow("AAPL", "40"))

    assert matching_lab([TECH, CORE], shown) is CORE


def test_matching_lab_reads_weights_as_the_form_does():
    shown = (MixRow("VWCE.DE", "60%"), MixRow("AAPL", "40,0"))

    assert matching_lab([CORE], shown) is CORE


def test_matching_lab_is_none_for_another_order_or_weight():
    assert matching_lab([CORE], (MixRow("AAPL", "40"), MixRow("VWCE.DE", "60"))) is None
    assert matching_lab([CORE], (MixRow("VWCE.DE", "50"), MixRow("AAPL", "50"))) is None
    assert matching_lab([CORE], ()) is None
    assert matching_lab(None, (MixRow("MSFT", "100"),)) is None


def test_mix_summary_lists_each_asset_with_its_weight():
    assert mix_summary(CORE) == "VWCE.DE 60% · AAPL 40%"


def test_view_params_drop_the_status_parameters_only():
    items = [
        ("ticker", "A"),
        ("saved", "1"),
        ("weight", "1"),
        ("lab_deleted", "0"),
        ("period", "5Y"),
    ]

    assert view_params(items) == [("ticker", "A"), ("weight", "1"), ("period", "5Y")]


def test_back_query_keeps_the_view_and_drops_status_and_remove():
    items = [("ticker", "A"), ("weight", "1"), ("remove", "0"), ("saved", "1"), ("period", "5Y")]

    assert back_query(items) == "ticker=A&weight=1&period=5Y"


def test_status_message_names_the_saved_lab_from_portfolios_data():
    assert status_message({"saved": "1"}, CORE) == (
        "success",
        "Saved.",
        "This mix is your lab Core.",
    )
    assert status_message({"saved": "1"}, None) == ("success", "Saved.", "")


def test_status_message_says_whether_a_delete_found_its_lab():
    assert status_message({"lab_deleted": "1"}, None) == ("success", "Lab deleted.", "")
    assert status_message({"lab_deleted": "0"}, None) == (
        "warning",
        "Nothing deleted.",
        "That lab no longer exists.",
    )


def test_status_message_is_none_without_a_status_parameter():
    assert status_message({}, CORE) is None
    assert status_message({"saved": "yes"}, CORE) is None
