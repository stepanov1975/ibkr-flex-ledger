"""Signed commissions, overlapping subtotals and coverage checks."""

from datetime import date
from decimal import Decimal as D

from app.analytics.account_commissions import account_commissions
from test_account_insights import row


def detail(**changes):
    return row("UnbundledCommissionDetails", **{
        "conid": "1", "tradeID": "T", "symbol": "TEST", "currency": "USD", "totalCommission": "-3",
        "brokerExecutionCharge": "-2", "brokerClearingCharge": "0", "thirdPartyExecutionCharge": "0",
        "thirdPartyClearingCharge": "0", "thirdPartyRegulatoryCharge": "-1", "other": "0",
        "regSection31TransactionFee": "-1", "regFINRATradingActivityFee": "0", "regOther": "0", **changes})


def trade(**changes):
    return {"payload": {"tradeID": "T", "conid": "1", "ibCommissionCurrency": "USD"},
            "currency": "USD", "commission": D("-3"), "event_id": "e", "instrument_id": "i",
            "report_date_local": date(2026, 9, 18), **changes}


def test_regulatory_subtotal_not_counted_twice_and_coverage_explicit():
    rows = [detail(), row("CashReport", fromDate="20260901", toDate="20260918")]
    r = account_commissions(rows, [trade(), trade(event_id="missing", payload={"tradeID": "OTHER"})])
    assert r["details"][0]["components_check"]["status"] == "matched"
    assert r["details"][0]["execution_check"]["status"] == "matched"
    assert r["totals"][0]["total"] == D("-3")
    assert r["covered_executions"] == 1
    assert r["missing_detail_executions"] == 1


def test_rebates_keep_positive_sign_and_currency_mismatch_is_unknown():
    d = detail(totalCommission="1", brokerExecutionCharge="2", thirdPartyRegulatoryCharge="-1")
    assert account_commissions([d], [trade(commission=D("1"))])["details"][0]["execution_check"]["status"] == "matched"
    t = trade()
    t["payload"]["ibCommissionCurrency"] = "EUR"
    assert account_commissions([d], [t])["details"][0]["execution_check"]["status"] == "not_comparable"


def test_duplicate_details_and_missing_components_do_not_false_match():
    d = detail()
    r = account_commissions([d, d], [trade()])
    assert r["totals"][0]["total"] is None
    assert all(v["execution_check"]["status"] == "not_comparable" for v in r["details"])
    d.payload.pop("other")
    assert account_commissions([d], [])["details"][0]["components_check"]["status"] == "not_comparable"
