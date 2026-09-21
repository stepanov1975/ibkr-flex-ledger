"""Pending income and payment matching regressions."""

from decimal import Decimal as D

from app.analytics.account_income import account_income
from test_account_insights import row


def accrual(**changes):
    return row("OpenDividendAccruals", **{"conid": "1", "actionID": "A", "symbol": "TEST",
               "currency": "USD", "grossAmount": "10", "tax": "2", "fee": "0", "netAmount": "8",
               "payDate": "20260920", **changes})


def test_pending_income_is_not_paid_and_history_is_not_summed():
    a = accrual()
    report = account_income([a], [a, a], [])
    assert report["pending_totals"] == [{"currency": "USD", "net": D("8")}]
    assert len(report["payment_checks"]) == 1
    assert report["payment_checks"][0]["check"]["status"] == "not_comparable"


def test_action_linked_payments_taxes_and_corrections():
    a = accrual()
    cash = [{"payload": {"actionID": "A", "conid": "1"}, "currency": "USD",
             "cash_action": "Dividends", "amount": D("10"), "raw_id": "pay"},
            {"payload": {"actionID": "A", "conid": "1"}, "currency": "USD",
             "cash_action": "Withholding Tax", "amount": D("-2"), "raw_id": "tax"}]
    assert account_income([], [a], cash)["payment_checks"][0]["check"]["status"] == "matched"
    cash[0]["amount"] = D("5")
    assert account_income([], [a], cash)["payment_checks"][0]["check"]["status"] == "different"
    cash[0]["payload"]["actionID"] = "another"
    assert account_income([], [a], cash)["payment_checks"][0]["check"]["status"] == "not_comparable"


def test_reversal_alone_is_not_payment_and_interest_rolls_forward():
    a = accrual()
    reversal = row("ChangeInDividendAccruals", actionID="A", conid="1", currency="USD", code="Re")
    interest = row("InterestAccruals", currency="BASE_SUMMARY", startingAccrualBalance="10",
                   interestAccrued="5", accrualReversal="-8", fxTranslation="0", endingAccrualBalance="7")
    report = account_income([reversal, interest], [a], [])
    assert report["payment_checks"][0]["reversal_seen"]
    assert report["payment_checks"][0]["check"]["status"] == "not_comparable"
    assert report["interest"][0]["check"]["status"] == "matched"
    interest.payload.pop("interestAccrued")
    assert account_income([interest], [], [])["interest"][0]["check"]["status"] == "not_comparable"


def test_review_ambiguous_entitlements_and_missing_action_stay_visible():
    from dataclasses import replace
    a = accrual()
    b = replace(accrual(netAmount="4"), raw_id="other")
    report = account_income([], [a, b], [])
    assert report["payment_checks"][0]["net"] is None
    assert report["payment_checks"][0]["check"]["status"] == "not_comparable"
    a.payload.pop("actionID")
    assert len(account_income([], [a], [])["payment_checks"]) == 1


def test_review_booked_cash_deductions_are_not_ignored():
    cash = [{"payload": {"actionID": "A", "conid": "1"}, "currency": "USD",
             "cash_action": "Dividends", "amount": D("10"), "fees": None,
             "withholding_tax": D("2"), "raw_id": "pay"}]
    check = account_income([], [accrual()], cash)["payment_checks"][0]["check"]
    assert check["calculated"] == D("8")
    assert check["status"] == "matched"
