"""What portfolio knows about held instruments that data-store can't tell it (risk spec §8).

**ETCs.** data-store's `asset_type` is wrong for some of them: `tracked_tickers` stores EGLN.L
and ISLN.L as `equity` (the risk thread's Discovery). An ETC is a commodity exposure, and its
currency exposure is the commodity's pricing currency (USD for gold and silver), not the
currency it's listed in. So the look-through never reads `asset_type` for these: an ETC is
whatever this table says.

**Hedged share classes.** A currency-hedged ETF's constituent currencies aren't its currency
exposure, and no data source says which share class is hedged. A ticker here goes into the
currency view as "hedged: <ticker>" instead of through its constituents. RK-22's CHECK found
no hedged holding, so the table is empty.

To add an instrument, add a row and a test case; a change here is a code change on purpose,
since a wrong row silently moves exposure between buckets.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Etc:
    commodity: str  # "gold", "silver": the sector bucket is "Commodity: <commodity>"
    currency: str  # the ISO code the commodity is priced in


ETCS: dict[str, Etc] = {
    "EGLN.L": Etc(commodity="gold", currency="USD"),  # iShares Physical Gold
    "SGLD.MI": Etc(commodity="gold", currency="USD"),  # iShares Physical Gold (Milan)
    "ISLN.L": Etc(commodity="silver", currency="USD"),  # iShares Physical Silver
}

# ticker -> the currency its share class is hedged to.
HEDGED: dict[str, str] = {}
