import pytest
from web.rebuild_outcome import rebuild_outcome

_RETURNS_LATER = "they'll catch up on the next scheduled refresh."


@pytest.mark.parametrize("positions", ["ok", "nothing_to_do"])
@pytest.mark.parametrize("returns", ["ok", "nothing_to_do"])
def test_both_rebuilds_done(positions, returns):
    assert rebuild_outcome(positions, returns) == "Holdings and returns updated."


@pytest.mark.parametrize("positions", ["ok", "nothing_to_do"])
def test_some_returns_waiting_for_prices(positions):
    assert (
        rebuild_outcome(positions, "partial")
        == "Holdings updated. Some returns are waiting for prices."
    )


@pytest.mark.parametrize("returns", ["skipped", "failed"])
def test_returns_not_updated_will_catch_up(returns):
    assert (
        rebuild_outcome("ok", returns)
        == f"Holdings updated. Returns couldn't be updated now; {_RETURNS_LATER}"
    )


def test_holdings_updated_when_returns_status_is_missing():
    assert rebuild_outcome("ok", None) == "Holdings updated."


@pytest.mark.parametrize(
    ("returns", "tail"),
    [
        ("ok", "Returns updated."),
        ("nothing_to_do", "Returns updated."),
        ("partial", "Some returns are waiting for prices."),
        ("skipped", f"Returns couldn't be updated now; {_RETURNS_LATER}"),
        ("failed", f"Returns couldn't be updated now; {_RETURNS_LATER}"),
        (None, None),
    ],
)
def test_some_holdings_waiting_for_a_mapping(returns, tail):
    lead = "Some holdings are waiting for a ticker mapping."
    expected = lead if tail is None else f"{lead} {tail}"
    assert rebuild_outcome("partial", returns) == expected


@pytest.mark.parametrize("returns", ["skipped", "failed", None])
def test_holdings_failed_never_promises_the_scheduled_refresh(returns):
    # The scheduled refresh rebuilds returns only, so positions catch up on the next write.
    message = rebuild_outcome("failed", returns)
    assert message == "Holdings couldn't be updated now; they'll update after your next change."
    assert "scheduled" not in message


@pytest.mark.parametrize(
    ("positions", "returns"),
    [(None, None), ("skipped", "skipped"), ("bogus", "ok"), ("<b>", "ok"), (None, "ok")],
)
def test_no_message_when_nothing_was_rebuilt_or_the_status_is_unknown(positions, returns):
    assert rebuild_outcome(positions, returns) == ""


def test_an_unknown_returns_status_is_ignored():
    assert rebuild_outcome("ok", "bogus") == "Holdings updated."
