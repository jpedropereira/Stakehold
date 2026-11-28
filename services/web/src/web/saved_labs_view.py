"""Saved labs on /labs (LF-1): pure, like labs_mix.py. Opening a lab is a link to the mix in the
query string, so the URL stays the page's whole state, as LB-7 made it.

The status a save or delete redirects with (`saved`, `lab_deleted`) is a flag, never a name: a
crafted link can't put words in the banner, the rule account delete follows. The saved lab's name
comes from portfolio, as the saved lab whose mix the page is showing. Every link and form on the
page leaves the flags behind (`view_params`), so the banner doesn't follow the reader around.
"""

from collections.abc import Iterable, Mapping, Sequence
from urllib.parse import urlencode

from web.labs_mix import MixRow, canonical_query, parse_weight
from web.portfolio_client.base import SavedLab

STATUS_PARAMS = frozenset({"saved", "lab_deleted"})


def lab_rows(lab: SavedLab) -> tuple[MixRow, ...]:
    return tuple(MixRow(ticker, weight) for ticker, weight in lab.assets)


def open_url(lab: SavedLab) -> str:
    """The lab's mix on /labs, as the mix form would submit it."""
    return "/labs?" + canonical_query(lab_rows(lab), ())


def _weighed(rows: Iterable[MixRow]) -> list:
    return [(row.ticker, parse_weight(row.weight)) for row in rows]


def matching_lab(labs: Sequence[SavedLab] | None, shown: Sequence[MixRow]) -> SavedLab | None:
    """The saved lab with exactly the mix the page shows: the same tickers in the same order, at
    the same weights read as numbers (so "60.00" is 60). None when no lab matches, when nothing is
    shown, or when the labs couldn't be loaded."""
    if not labs or not shown:
        return None
    target = _weighed(shown)
    return next((lab for lab in labs if _weighed(lab_rows(lab)) == target), None)


def mix_summary(lab: SavedLab) -> str:
    """What a delete confirms, beside the lab's name: "VWCE.DE 60% · AAPL 40%"."""
    return " · ".join(f"{ticker} {weight}%" for ticker, weight in lab.assets)


def view_params(items: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """The query without the save and delete status flags."""
    return [(k, v) for k, v in items if k not in STATUS_PARAMS]


def back_query(items: Iterable[tuple[str, str]]) -> str:
    """The view to return to after a save or delete: the query without the status flags and
    without a pending `remove`, which the page answers with a redirect."""
    return urlencode([(k, v) for k, v in view_params(items) if k != "remove"])


def status_message(
    params: Mapping[str, str], current: SavedLab | None
) -> tuple[str, str, str] | None:
    """(banner kind, bold lead, rest) for a save or delete just done, or None."""
    if params.get("saved") == "1":
        return "success", "Saved.", f"This mix is your lab {current.name}." if current else ""
    deleted = params.get("lab_deleted")
    if deleted == "1":
        return "success", "Lab deleted.", ""
    if deleted == "0":
        return "warning", "Nothing deleted.", "That lab no longer exists."
    return None
