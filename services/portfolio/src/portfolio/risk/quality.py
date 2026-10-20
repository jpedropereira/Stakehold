"""Data-quality accounting and periods per year.

- **Gap budget (R8).** Every figure records `n_obs` (non-NULL returns) and `n_gaps` (NULLs
  excluded). Gaps above 2% of the expected observations, `n_obs + n_gaps`, raise `data_gaps`.
- **Suspect rows (§4.3).** data-store nulls every single-day return above 100% that has a
  mechanical signature (a zero-volume day, a dividend glitch), and deliberately keeps the ~17
  that have none. So any non-NULL return above 100% in magnitude is one of those kept rows, and
  `has_suspect_rows` is exactly that test. See data-store's docs/schema.md, "Implausible
  single-day returns are suppressed, not corrected".
- **q (§4.4).** 52 for weekly. For daily, the empirical observations per year over the span
  from the base date to the last date, so different calendars (a union calendar, one exchange)
  annualise on a comparable footing. A NULL day still counts toward q: it is a day of the
  calendar whose move was lost, not a day that didn't exist.
"""

from portfolio.risk.types import Frequency, ReturnSeries

WEEKLY_Q = 52
_DAYS_PER_YEAR = 365.25
_SUSPECT_THRESHOLD = 1.0
# 2%, compared in integers so the boundary is exact.
_GAP_BUDGET_DENOMINATOR = 50


def gap_stats(series: ReturnSeries) -> tuple[int, int]:
    """(n_obs, n_gaps)."""
    n_gaps = sum(1 for v in series.values if v is None)
    return len(series.values) - n_gaps, n_gaps


def over_gap_budget(n_obs: int, n_gaps: int) -> bool:
    return n_gaps * _GAP_BUDGET_DENOMINATOR > n_obs + n_gaps


def has_suspect_rows(series: ReturnSeries) -> bool:
    return any(v is not None and abs(v) > _SUSPECT_THRESHOLD for v in series.values)


def empirical_q(series: ReturnSeries) -> float:
    if not series.dates:
        raise ValueError("empirical q needs at least one observation")
    years = (series.last_date - series.base_date).days / _DAYS_PER_YEAR
    return len(series.values) / years


def periods_per_year(series: ReturnSeries) -> float:
    if series.frequency is Frequency.WEEKLY:
        return WEEKLY_Q
    return empirical_q(series)
