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


def test_settled_cash_excludes_base_and_preserves_negative_and_unknown():
    from app.analytics.account_holdings import account_settled_cash
    rows = [row("CashReport", currency="BASE_SUMMARY", endingCash="100", endingSettledCash="100"),
            row("CashReport", currency="USD", endingCash="-10", endingSettledCash="-15"),
            row("CashReport", currency="EUR", endingCash="20")]
    report = account_settled_cash(rows)
    assert len(report) == 2
    assert report[0]["ending"] == D("-10")
    assert report[0]["unsettled"] == D("5")
    assert report[1]["settled"] is None
    assert report[1]["unsettled"] is None


def test_concentration_signed_weights_and_missing_fx():
    from app.analytics.account_holdings import account_concentration
    rows = [row("EquitySummaryInBase", total="1000", currency="USD", reportDate="20260918"),
            row("OpenPositions", conid="1", symbol="SHORT", assetCategory="OPT", currency="USD",
                positionValue="-100", percentOfNAV="100"),
            row("OpenPositions", conid="2", symbol="LONG", assetCategory="STK", currency="EUR",
                positionValue="200", fxRateToBase="1.5", percentOfNAV="100")]
    report = account_concentration(rows)
    assert [r["symbol"] for r in report["holdings"]] == ["LONG", "SHORT"]
    assert all(r["check"]["status"] == "matched" for r in report["holdings"])
    assert report["holdings"][0]["calculated_percent"] == D("30")
    assert report["holdings"][1]["calculated_percent"] == D("-10")
    rows[-1].payload.pop("fxRateToBase")
    report = account_concentration(rows)
    assert next(r for r in report["currency_allocation"] if r["currency"] == "EUR")["value_usd"] is None


def test_concentration_zero_nav_and_duplicate_rows_are_unknown():
    from app.analytics.account_holdings import account_concentration
    nav = row("EquitySummaryInBase", total="0", currency="USD", reportDate="20260918")
    position = row("OpenPositions", conid="1", symbol="TEST", positionValue="20", currency="USD")
    assert account_concentration([nav, position])["holdings"][0]["calculated_percent"] is None
    nav.payload["total"] = "100"
    assert all(r["value_usd"] is None for r in account_concentration([nav, position, position])["holdings"])
