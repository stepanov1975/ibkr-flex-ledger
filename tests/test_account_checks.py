"""Independent account check regression cases."""

from datetime import date, datetime, timezone
from decimal import Decimal as D

from app.analytics.account_checks import account_calculation_checks
from test_account_insights import row


def evidence():
    return {"instruments": [{"conid": "1", "instrument_id": "i", "symbol": "TEST", "asset_category": "STK"}],
            "lots": [{"instrument_id": "i", "remaining_quantity": D("2"), "cost_basis_remaining": D("80"), "side": "BUY"}],
            "snapshots": [{"instrument_id": "i", "report_date_local": date(2026, 9, 18), "currency": "USD",
                           "calculated_at_utc": datetime(2026, 9, 19, tzinfo=timezone.utc),
                           "fx_dependencies": [], "provisional": False, "realized_pnl": D("10")}],
            "mutation": None, "trades": [], "cash": []}


def positions():
    return [row("OpenPositions", conid="1", symbol="TEST", currency="USD", position="2",
                positionValue="100", costBasisMoney="80", fifoPnlUnrealized="20")]


def test_quantity_is_from_lots_not_broker_authoritative_snapshot():
    e = evidence()
    e["snapshots"][0]["position_qty"] = D("999")
    checks = account_calculation_checks(positions(), e)
    assert all(c["status"] == "matched" for c in checks)
    e["lots"][0]["remaining_quantity"] = D("1")
    assert account_calculation_checks(positions(), e)[0]["status"] == "different"


def test_short_lots_signed_and_stale_projection_unknown():
    e = evidence()
    e["lots"][0].update(side="SELL", cost_basis_remaining=D("-80"))
    p = positions()
    p[0].payload.update(position="-2", positionValue="-100", costBasisMoney="-80", fifoPnlUnrealized="-20")
    assert all(c["status"] == "matched" for c in account_calculation_checks(p, e))
    e["mutation"] = datetime(2026, 9, 20, tzinfo=timezone.utc)
    assert all(c["status"] == "not_comparable" for c in account_calculation_checks(p, e))


def test_cash_rollforward_and_fx_coverage():
    rows = positions() + [row("CashReport", currency="USD", fromDate="20260901", toDate="20260918",
                              startingCash="100", endingCash="80")]
    e = evidence()
    e["trades"] = [{"instrument_id": "i", "report_date_local": date(2026, 9, 10),
                    "updated_at_utc": datetime(2026, 9, 10, tzinfo=timezone.utc),
                    "net_cash": D("-20"), "currency": "USD", "payload": {"assetCategory": "STK"}}]
    result = account_calculation_checks(rows, e)
    assert result[-1]["status"] == "matched"
    e["trades"][0]["payload"]["assetCategory"] = "CASH"
    assert account_calculation_checks(rows, e)[-1]["status"] == "not_comparable"


def test_fifo_summary_requires_opening_snapshot_and_removes_income():
    rows = positions() + [row("CashReport", currency="USD", fromDate="20260901", toDate="20260918"),
                           row("FIFOPerformanceSummaryInBase", conid="1", totalRealizedPnl="8",
                               totalUnrealizedPnl="20"),
                           row("MTMPerformanceSummaryInBase", conid="1", total="5", priorOpenMtm="1",
                               transactionMtm="6", commissions="-2", other="0")]
    e = evidence()
    checks = account_calculation_checks(rows, e)
    assert next(c for c in checks if c["name"] == "Period realized trading P&L")["status"] == "not_comparable"
    e["snapshots"].append({**e["snapshots"][0], "report_date_local": date(2026, 8, 31), "realized_pnl": D("2")})
    assert next(c for c in account_calculation_checks(rows, e) if c["name"] == "Period realized trading P&L")["status"] == "matched"
    assert checks[-1]["name"] == "MTM component arithmetic"
    assert checks[-1]["status"] == "matched"


def test_review_nullable_cash_deductions_and_unknown_opening():
    rows = positions() + [row("CashReport", currency="USD", fromDate="20260901", toDate="20260918",
                              startingCash="100", endingCash="103"),
                           row("FIFOPerformanceSummaryInBase", conid="1", totalRealizedPnl="8")]
    e = evidence()
    e["cash"] = [{"instrument_id": "i", "currency": "USD", "amount": D("3"), "amount_in_base": None,
                  "fees": None, "withholding_tax": None, "report_date_local": date(2026, 9, 10),
                  "updated_at_utc": datetime(2026, 9, 10, tzinfo=timezone.utc), "payload": {}}]
    e["snapshots"].append({**e["snapshots"][0], "report_date_local": date(2026, 8, 31),
                           "realized_pnl": D("-1"), "calculated_at_utc": None})
    checks = account_calculation_checks(rows, e)
    assert checks[-1]["status"] == "matched"
    assert next(c for c in checks if c["name"] == "Period realized trading P&L")["status"] == "not_comparable"


def test_review_lot_only_and_duplicate_summary_are_not_broker_zero():
    p = positions()
    p[0].payload["levelOfDetail"] = "LOT"
    assert all(c["status"] == "not_comparable" for c in account_calculation_checks(p, evidence()))
    p[0].payload["levelOfDetail"] = "SUMMARY"
    assert all(c["status"] == "not_comparable" for c in account_calculation_checks(p + p, evidence()))


def test_review_ambiguous_position_cannot_match_fifo_summary():
    rows = positions() + positions() + [
        row("CashReport", fromDate="20260901", toDate="20260918"),
        row("FIFOPerformanceSummaryInBase", conid="1", totalUnrealizedPnl="20")]
    checks = account_calculation_checks(rows, evidence())
    assert next(c for c in checks if c["name"] == "Summary unrealized P&L")["status"] == "not_comparable"
