"""Option lifecycle identity, multiplier and ambiguity tests."""

from decimal import Decimal as D

from app.analytics.account_options import account_option_activity
from test_account_insights import row


def option(**changes):
    return row("OptionEAE", **{"conid": "o", "tradeID": "T", "symbol": "PUT", "assetCategory": "OPT",
        "date": "20260918", "currency": "USD", "quantity": "1", "proceeds": "0", "multiplier": "50",
        "transactionType": "Assignment", "putCall": "P", "underlyingConid": "s", **changes})


def test_unique_option_and_adjusted_multiplier_delivery():
    trades = [{"payload": {"tradeID": "T", "conid": "o"}, "currency": "USD",
               "quantity": D("1"), "price": D("0"), "side": "BUY", "instrument_id": "i", "event_id": "e"}]
    leg = row("OptionEAE", conid="s", date="20260918", currency="USD", transactionType="Buy", quantity="50")
    result = account_option_activity([option(), leg], trades)[0]
    assert result["event_id"] == "e"
    assert result["quantity_check"]["status"] == result["cash_check"]["status"] == result["delivery"]["status"] == "matched"
    assert result["delivery"]["calculated"] == D("50")


def test_ambiguous_trade_and_underlying_legs_are_not_matched():
    leg = row("OptionEAE", conid="s", date="20260918", currency="USD", transactionType="Buy", quantity="50")
    result = account_option_activity([option(), leg, leg], [])[0]
    assert result["instrument_id"] is None
    assert result["quantity_check"]["status"] == result["delivery"]["status"] == "not_comparable"


def test_expiration_does_not_invent_stock_delivery():
    result = account_option_activity([option(transactionType="Expiration")], [])[0]
    assert result["delivery"] is None
