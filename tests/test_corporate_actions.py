"""Corporate-action allowlist and adjustment regression tests."""

from decimal import Decimal

import pytest

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



@pytest.mark.parametrize("code,payload,factor", [
    ("FS", {"description": "SNEX(US8618961085) SPLIT 3 FOR 2 (SNEX, STONEX GROUP INC, US8618961085)"}, "1.5"),
    ("RS", {"description": "TEST(US1) SPLIT 1 FOR 10 (TEST, TEST INC, US1)"}, "0.1"),
    ("FS", {"description": "SPLIT 3 FOR 0 (TEST)"}, None),
    ("FS", {"description": "SPLIT 3 FOR 2 (TEST) SPLIT 4 FOR 1 (TEST)"}, None),
    ("SO", {"description": "SPINOFF 1 FOR 60 (TEST)"}, None),
    ("FS", {"ratio": "NaN"}, None),
    ("FS", {"ratio": "Infinity"}, None),
    ("FS", {"ratio": "-Infinity"}, None),
])
def test_explicit_split_description_and_invalid_factors(code, payload, factor):
    result = domain_classify_corporate_action(code, payload)
    assert result.requires_manual is (factor is None)
    assert result.adjustment_factor == (None if factor is None else Decimal(factor))


@pytest.mark.parametrize("code,new,old", [("RS", "3", "2"), ("FS", "1", "10"), ("SD", "1", "10"), ("RS", "1", "1"), ("FS", "1", "1")])
@pytest.mark.parametrize("source", ["description", "ratio", "quantities"])
def test_split_factor_must_match_action_direction(code, new, old, source):
    payload = {
        "description": {"description": f"SPLIT {new} FOR {old} (TEST)"},
        "ratio": {"ratio": str(Decimal(new) / Decimal(old))},
        "quantities": {"newQuantity": new, "oldQuantity": old},
    }[source]
    result = domain_classify_corporate_action(code, payload)
    assert result.requires_manual is True
    assert result.adjustment_factor is None
