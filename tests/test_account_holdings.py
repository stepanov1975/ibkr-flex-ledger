"""Signed lending and cash display regressions."""

from decimal import Decimal as D

from app.analytics.account_holdings import account_lending
from test_account_insights import row


def test_lending_preserves_owned_shares_and_signed_arithmetic():
    rows = [row("NetStockPositionSummary", conid="1", symbol="TEST", sharesAtIb="100",
                sharesBorrowed="0", sharesLent="-40", netShares="60"),
            row("OpenPositions", conid="1", position="100")]
    r = account_lending(rows)["holdings"][0]
    assert r["owned"] == D("100")
    assert r["lent"] == D("40")
    assert r["net"] == D("60")
    assert r["lent_percent"] == D("40")
    assert r["net_check"]["status"] == r["holding_check"]["status"] == "matched"


def test_lending_missing_values_and_zero_owned():
    r = account_lending([row("NetStockPositionSummary", conid="1", sharesAtIb="0",
                             sharesLent="-5", netShares="-5")])["holdings"][0]
    assert r["lent_percent"] is None
    assert r["net_check"]["status"] == r["holding_check"]["status"] == "not_comparable"
