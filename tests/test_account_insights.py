"""Account insight accounting checks with synthetic broker data."""

from datetime import date
from decimal import Decimal

from app.analytics.account_insights import AccountInsights, InsightRow, _CHANGE_COMPONENTS, _NAV_COMPONENTS, insight_decimal


def row(section, **payload):
    return InsightRow(section, payload, section + "-raw", "artifact", date(2026, 9, 18))


def sample():
    rows = [
        row("EquitySummaryInBase", reportDate="20260918", currency="USD", total="155",
            cash="50", stock="100", dividendAccruals="3", interestAccruals="2",
            slbCashCollateral="20", slbDirectSecuritiesLent="-20"),
        row("CashReport", currency="BASE_SUMMARY", endingCash="50"),
        row("OpenPositions", conid="1", currency="USD", positionValue="100", levelOfDetail="SUMMARY"),
    ]
    rows[0].payload.update({key: "0" for key in _NAV_COMPONENTS if key not in rows[0].payload})
    return rows


def test_nav_explains_accruals_and_offsets_collateral():
    result = AccountInsights(sample(), []).build()["nav"]
    assert result["cash_and_positions"] == Decimal("150")
    assert result["additional_components"] == Decimal("5")
    assert result["check"]["status"] == "matched"


def test_nav_missing_fx_or_date_does_not_match():
    rows = sample()
    rows[-1].payload.update(currency="EUR")
    assert AccountInsights(rows, []).nav()["check"]["status"] == "not_comparable"
    rows[-1].payload.update(currency="USD")
    rows[0].payload.update(reportDate="20260917")
    assert AccountInsights(rows, []).nav()["check"]["status"] == "not_comparable"


def test_duplicate_position_detail_does_not_double_count():
    rows = sample()
    rows.append(rows[-1])
    assert AccountInsights(rows, []).nav()["cash_and_positions"] is None


def test_nav_detects_unexplained_difference_and_missing_numbers():
    rows = sample()
    rows[0].payload["total"] = "160"
    assert AccountInsights(rows, []).nav()["check"]["difference"] == Decimal("-5")
    rows[1].payload["endingCash"] = ""
    assert AccountInsights(rows, []).nav()["check"]["status"] == "not_comparable"
    for value in ("N/A", "NaN", "Infinity", "invalid", None):
        assert insight_decimal(value) is None
    assert insight_decimal("1,234.50") == Decimal("1234.50")


def test_change_nav_modes_twr_and_unknown_components():
    r = row("ChangeInNAV", startingValue="100", endingValue="112", mtm="10", realized="0",
            changeInUnrealized="0", dividends="2", twr="12", currency="USD")
    r.payload.update({key: "0" for key in _CHANGE_COMPONENTS if key not in r.payload})
    report = AccountInsights([r], []).change_in_nav()
    assert report["check"]["status"] == "matched"
    assert report["twr_percent"] == Decimal("12")
    r.payload.update(mtm="0", realized="4", changeInUnrealized="6")
    assert AccountInsights([r], []).change_in_nav()["check"]["status"] == "matched"
    r.payload.update(mtm="10")
    assert AccountInsights([r], []).change_in_nav()["check"]["status"] == "not_comparable"
    r.payload.update(mtm="0", newComponent="1")
    assert AccountInsights([r], []).change_in_nav()["unknown_fields"] == ["newComponent"]


def test_empty_and_dated_history():
    assert AccountInsights([], []).build()["nav"] is None
    report = AccountInsights([], [row("EquitySummaryInBase", reportDate="20260918", total="100")]).build()
    assert report["nav_history"][0]["date"] == date(2026, 9, 18)


def test_review_missing_realized_mode_leg_is_unknown():
    r = row("ChangeInNAV", startingValue="100", endingValue="110", realized="10")
    assert AccountInsights([r], []).change_in_nav()["check"]["status"] == "not_comparable"


def test_review_summary_and_lot_rows_do_not_double_count():
    rows = sample() + [row("OpenPositions", conid="1", levelOfDetail="LOT", positionValue="100", currency="USD")]
    assert AccountInsights(rows, []).nav()["check"]["status"] == "matched"


def test_review_history_normalizes_dates_and_preserves_newest_precedence():
    history = [row("EquitySummaryInBase", reportDate="09/18/2026", total="200"),
               row("EquitySummaryInBase", reportDate="20260918", total="100"),
               row("EquitySummaryInBase", reportDate="20260901", total="50")]
    result = AccountInsights([], history).build()["nav_history"]
    assert [r["date"] for r in result] == [date(2026, 9, 1), date(2026, 9, 18)]
    assert result[-1]["nav"] == Decimal("200")


def test_missing_nav_component_cannot_match_even_when_remaining_values_balance():
    rows = sample()
    del rows[0].payload["interestAccruals"]
    rows[0].payload["total"] = "153"
    check = AccountInsights(rows, []).nav()["check"]
    assert check["status"] == "not_comparable"
    assert "interestAccruals" in check["reason"]


def test_missing_nav_movement_cannot_match_even_when_remaining_values_balance():
    payload = dict.fromkeys(_CHANGE_COMPONENTS, "0")
    payload.update(startingValue="100", endingValue="110", mtm="10")
    r = row("ChangeInNAV", **payload)
    assert AccountInsights([r], []).change_in_nav()["check"]["status"] == "matched"
    del r.payload["depositsWithdrawals"]
    check = AccountInsights([r], []).change_in_nav()["check"]
    assert check["status"] == "not_comparable"
    assert "depositsWithdrawals" in check["reason"]
