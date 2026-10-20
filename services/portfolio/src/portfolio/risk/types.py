"""The risk library's shared types. The rules are in docs/domain/risk.md (the spec), cited by
section and rule number.

The library is pure: no I/O, no datastore_client calls, no logging. Callers fetch the data,
convert it to EUR (RK-5) and hand it in as plain floats. Risk figures are statistical estimates,
not ledger amounts, so they are floats, and numpy does the arithmetic. Money stays in Decimal
everywhere else in portfolio.

A NULL return is a gap (R5): it is `None` here, NaN inside numpy, and never 0, which is a real
flat day.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import numpy as np


class Frequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"


@dataclass(frozen=True)
class ReturnSeries:
    """Returns at one frequency. `base_date` is the date of G0, the level the first return is
    measured from: CAGR's span and the first risk-free period both start there."""

    frequency: Frequency
    base_date: date
    dates: tuple[date, ...]
    values: tuple[float | None, ...]

    def __post_init__(self):
        if len(self.dates) != len(self.values):
            raise ValueError(
                f"{len(self.dates)} dates but {len(self.values)} values in a return series"
            )
        previous = self.base_date
        for day in self.dates:
            if day <= previous:
                raise ValueError(
                    f"return series dates must strictly increase after the base date "
                    f"{self.base_date}: {day} follows {previous}"
                )
            previous = day

    @property
    def last_date(self) -> date:
        return self.dates[-1] if self.dates else self.base_date


@dataclass(frozen=True)
class Window:
    """A measurement window: returns dated after `start` and on or before `end`, measured from
    the level on `start`."""

    label: str
    start: date
    end: date

    def __post_init__(self):
        if self.start > self.end:
            raise ValueError(f"window {self.label} starts {self.start}, after its end {self.end}")


class Flag(StrEnum):
    """Spec §9's flags, plus `data_gaps` for R8's 2% gap budget."""

    INSUFFICIENT_HISTORY = "insufficient_history"
    SHORT_WINDOW = "short_window"
    HAS_SUSPECT_ROWS = "has_suspect_rows"
    RF_PARTIAL = "rf_partial"
    SURVIVORSHIP = "survivorship"
    NONSYNC_DAILY = "nonsync_daily"
    TAIL_UNSTABLE = "tail_unstable"
    DATA_GAPS = "data_gaps"


@dataclass(frozen=True)
class RiskFigure:
    """One reported figure with the metadata §9 and §10 require. `value` is None exactly when
    the figure is greyed, and `reason` then says why (R9) -- never 0, never blank."""

    value: float | None
    window_start: date
    window_end: date
    frequency: Frequency
    q: float
    n_obs: int
    n_gaps: int
    flags: frozenset[Flag]
    reason: str | None = None


def to_array(series: ReturnSeries) -> np.ndarray:
    """The values as a float array, NaN where the series has a gap."""
    return np.array([np.nan if v is None else v for v in series.values], dtype=float)
