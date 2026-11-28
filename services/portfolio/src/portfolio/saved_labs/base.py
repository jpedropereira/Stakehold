"""A saved lab (LF-1): a named Labs mix, owned by one user. Pure: the types and the rules a lab is
checked against before anything is written. See docs/schema.md's `saved_labs` for the columns
the limits come from.

Weights are the percentages the user typed, unscaled: the backtest scales them to 100% (LB-7),
so a lab of "60 and 60" opens as typed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from portfolio.errors import InvalidSavedLab

# The backtest's own cap (api/models.MAX_BACKTEST_ASSETS): a lab is something to backtest.
MAX_LAB_ASSETS = 50
MAX_LAB_NAME = 100  # saved_labs.name is VARCHAR(100)
MAX_TICKER = 20  # saved_lab_assets.ticker is VARCHAR(20), data-store's ticker limit
# saved_lab_assets.weight_pct is NUMERIC(12,6): six places, and under 10^6.
_WEIGHT_PLACES = 6
_WEIGHT_LIMIT = Decimal("1000000")


@dataclass(frozen=True)
class LabAsset:
    ticker: str
    weight_pct: Decimal


@dataclass(frozen=True)
class SavedLab:
    name: str
    assets: tuple[LabAsset, ...]
    created_at: datetime
    updated_at: datetime


def _check_weight(weight: Decimal) -> None:
    if not weight.is_finite() or weight <= 0:
        raise InvalidSavedLab("Each weight must be above 0%.")
    if weight >= _WEIGHT_LIMIT:
        raise InvalidSavedLab("A weight must be under 1,000,000%.")
    if -weight.normalize().as_tuple().exponent > _WEIGHT_PLACES:
        raise InvalidSavedLab(f"A weight can have at most {_WEIGHT_PLACES} decimal places.")


def validate_lab(name: str, assets: Iterable[LabAsset]) -> tuple[str, tuple[LabAsset, ...]]:
    """The name and assets as they'll be stored, trimmed, in the order given. Raises
    InvalidSavedLab with a message for the user on the first rule broken."""
    name = name.strip()
    if not name:
        raise InvalidSavedLab("Enter a name for the lab.")
    if len(name) > MAX_LAB_NAME:
        raise InvalidSavedLab(f"A lab's name can be at most {MAX_LAB_NAME} characters.")
    checked = tuple(LabAsset(a.ticker.strip(), a.weight_pct) for a in assets)
    if not checked:
        raise InvalidSavedLab("A lab needs at least one asset.")
    if len(checked) > MAX_LAB_ASSETS:
        raise InvalidSavedLab(f"A lab can have at most {MAX_LAB_ASSETS} assets.")
    seen: set[str] = set()
    for asset in checked:
        if not 1 <= len(asset.ticker) <= MAX_TICKER:
            raise InvalidSavedLab(f"Each ticker must be 1 to {MAX_TICKER} characters.")
        if asset.ticker in seen:
            raise InvalidSavedLab(f"{asset.ticker} is in the lab twice.")
        seen.add(asset.ticker)
        _check_weight(asset.weight_pct)
    return name, checked
