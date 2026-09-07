"""Corporate-action allowlist and adjustment regression tests."""

from decimal import Decimal

from app.domain import domain_classify_corporate_action


def test_ambiguous_corporate_actions_require_manual_review() -> None:
    """Keep mergers and unknown actions provisional."""

    merger = domain_classify_corporate_action("TC", {})
    unknown = domain_classify_corporate_action("NEW_ACTION", {})

    assert merger.action_type == "MERGER"
    assert merger.requires_manual is True
    assert unknown.requires_manual is True


def test_split_is_automatic_only_with_deterministic_factor() -> None:
    """Auto-handle a split only when its quantity transform is explicit."""

    deterministic = domain_classify_corporate_action("FS", {"newQuantity": "4", "oldQuantity": "1"})
    ambiguous = domain_classify_corporate_action("FS", {})

    assert deterministic.requires_manual is False
    assert deterministic.adjustment_factor == Decimal("4")
    assert ambiguous.requires_manual is True


def test_cash_dividend_requires_review_until_cashflows_can_be_matched() -> None:
    """Require review even with amount and withholding until cashflow identity is known."""

    complete = domain_classify_corporate_action("CD", {"amount": "12.50", "withholdingTax": "0"})
    incomplete = domain_classify_corporate_action("CD", {"amount": "12.50"})

    assert complete.requires_manual is True
    assert incomplete.requires_manual is True
