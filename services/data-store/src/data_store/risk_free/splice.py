"""The risk measures' risk-free series, `rf_eur` (spec `docs/domain/risk.md` §4.1), joined from
the two raw series risk_free_rates stores.

- From ESTR_FIRST_DAY on: €STR as published.
- Before it: EONIA − EONIA_SPREAD, the fixed spread the ECB defined between the two when it
  redefined EONIA as €STR + 8.5 bp on 2019-10-01.

The date alone picks the series. EONIA kept being published until 2021-12-31, but those rows are
€STR + 0.085 by construction, so they're never served, not even on a day €STR is missing: the
caller forward-fills a missing day (§4.1), and serving the other series there would swap sources
mid-window. Pure, no I/O: read_flows fetches the rows.
"""

from collections.abc import Iterable
from datetime import date
from decimal import Decimal

ESTR_FIRST_DAY = date(2019, 10, 1)
EONIA_SPREAD = Decimal("0.085")

# risk_free_rates.rate is NUMERIC(8,4); both series come out at the column's own scale.
_RATE_SCALE = Decimal("1E-4")

EONIA_ADJUSTED = "eonia_adjusted"
ESTR = "estr"


def splice(
    eonia_rows: Iterable[tuple[date, Decimal]], estr_rows: Iterable[tuple[date, Decimal]]
) -> list[dict]:
    """(rate_date, rate) rows of each raw series -> rf_eur's points, in date order, each with
    the series it came from."""
    points = [
        _point(rate_date, rate - EONIA_SPREAD, EONIA_ADJUSTED)
        for rate_date, rate in eonia_rows
        if rate_date < ESTR_FIRST_DAY
    ]
    points += [
        _point(rate_date, rate, ESTR)
        for rate_date, rate in estr_rows
        if rate_date >= ESTR_FIRST_DAY
    ]
    return sorted(points, key=lambda point: point["date"])


def spliced_first_date(first_eonia: date | None, first_estr: date | None) -> date | None:
    """The first day rf_eur covers, from each raw series' first stored date. None when neither
    contributes anything, i.e. nothing usable is stored."""
    if first_eonia is not None and first_eonia < ESTR_FIRST_DAY:
        return first_eonia
    if first_estr is not None:
        return max(first_estr, ESTR_FIRST_DAY)
    return None


def _point(rate_date: date, rate: Decimal, series: str) -> dict:
    return {"date": rate_date, "rate": rate.quantize(_RATE_SCALE), "series": series}
