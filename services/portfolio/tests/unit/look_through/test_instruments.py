"""The ETC and hedged-ETF table (RK-22): what data-store's asset_type can't be trusted for."""

from portfolio.look_through.instruments import ETCS, HEDGED, Etc


def test_the_held_etcs_are_commodities_priced_in_usd():
    assert ETCS["EGLN.L"] == Etc(commodity="gold", currency="USD")
    assert ETCS["SGLD.MI"] == Etc(commodity="gold", currency="USD")
    assert ETCS["ISLN.L"] == Etc(commodity="silver", currency="USD")


def test_no_holding_is_known_to_be_hedged_yet():
    assert HEDGED == {}
