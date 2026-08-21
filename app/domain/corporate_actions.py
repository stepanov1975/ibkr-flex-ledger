"""Corporate-action classification rules from the frozen MVP allowlist."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class CorporateActionClassification:
    """Normalized corporate-action handling decision."""

    action_type: str
    requires_manual: bool
    adjustment_factor: Decimal | None


_ACTION_ALIASES = {
    "FS": "FORWARDSPLIT",
    "FORWARDSPLIT": "FORWARDSPLIT",
    "FORWARD SPLIT": "FORWARDSPLIT",
    "RS": "REVERSESPLIT",
    "REVERSESPLIT": "REVERSESPLIT",
    "REVERSE SPLIT": "REVERSESPLIT",
    "SD": "STOCKDIV",
    "STOCKDIV": "STOCKDIV",
    "STOCK DIVIDEND": "STOCKDIV",
    "CD": "CASHDIV",
    "CASHDIV": "CASHDIV",
    "CASH DIVIDEND": "CASHDIV",
    "SO": "SPINOFF",
    "SPINOFF": "SPINOFF",
    "TC": "MERGER",
    "MERGER": "MERGER",
    "RI": "RIGHTSISSUE",
    "SR": "RIGHTSISSUE",
    "RIGHTSISSUE": "RIGHTSISSUE",
    "CH": "CHOICEDIV",
    "HD": "CHOICEDIV",
    "HI": "CHOICEDIV",
    "CHOICEDIV": "CHOICEDIV",
    "GV": "GENERICVOLUNTARY",
    "GENERICVOLUNTARY": "GENERICVOLUNTARY",
}


def domain_classify_corporate_action(
    reorg_code: str,
    source_payload: dict[str, object],
) -> CorporateActionClassification:
    """Classify an action and require manual review unless auto conditions are explicit."""

    normalized_code = " ".join(reorg_code.strip().upper().replace("_", " ").split())
    action_type = _ACTION_ALIASES.get(normalized_code, normalized_code or "UNKNOWN")

    if action_type in {"FORWARDSPLIT", "REVERSESPLIT", "STOCKDIV"}:
        factor = _domain_corporate_action_factor(source_payload)
        return CorporateActionClassification(
            action_type=action_type,
            requires_manual=factor is None or factor <= Decimal("0"),
            adjustment_factor=factor,
        )

    if action_type == "CASHDIV":
        has_amount = _domain_payload_decimal(source_payload, "amount") is not None
        has_withholding = "withholdingTax" in source_payload
        return CorporateActionClassification(
            action_type=action_type,
            requires_manual=not (has_amount and has_withholding),
            adjustment_factor=None,
        )

    return CorporateActionClassification(
        action_type=action_type,
        requires_manual=True,
        adjustment_factor=None,
    )


def _domain_corporate_action_factor(source_payload: dict[str, object]) -> Decimal | None:
    direct_factor = _domain_payload_decimal(source_payload, "ratio")
    if direct_factor is not None:
        return direct_factor

    new_quantity = _domain_payload_decimal(source_payload, "newQuantity")
    old_quantity = _domain_payload_decimal(source_payload, "oldQuantity")
    if new_quantity is None or old_quantity is None or old_quantity == Decimal("0"):
        return None
    return new_quantity / old_quantity


def _domain_payload_decimal(source_payload: dict[str, object], field_name: str) -> Decimal | None:
    value = source_payload.get(field_name)
    if value is None or isinstance(value, bool):
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None
