"""Corporate-action classification rules from the frozen MVP allowlist."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re


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
        if factor is not None and not (
            Decimal("0") < factor < Decimal("1") if action_type == "REVERSESPLIT" else factor > Decimal("1")
        ):
            factor = None
        return CorporateActionClassification(
            action_type=action_type,
            requires_manual=factor is None,
            adjustment_factor=factor,
        )

    # CASHDIV needs explicit review: CorporateActions does not establish whether
    # CashTransactions already contains its payment and withholding. Only cash
    # transaction rows feed cash accounting until that identity is unambiguous.

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
    if new_quantity is not None and old_quantity is not None and old_quantity > 0:
        return new_quantity / old_quantity

    # Only accept the explicit broker split clause, never a spinoff ratio or
    # an inference from the quantity credited to the account.
    if any(str(source_payload.get(key) or "").strip() for key in ("ratio", "newQuantity", "oldQuantity")):
        return None
    matches = re.findall(
        r"\bSPLIT\s+([0-9]+(?:\.[0-9]+)?)\s+FOR\s+([0-9]+(?:\.[0-9]+)?)\s*\(",
        str(source_payload.get("description") or "").upper(),
    )
    if len(matches) == 1:
        new, old = map(Decimal, matches[0])
        if new > 0 and old > 0:
            return new / old
    return None


def _domain_payload_decimal(source_payload: dict[str, object], field_name: str) -> Decimal | None:
    value = source_payload.get(field_name)
    if value is None or isinstance(value, bool):
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    try:
        number = Decimal(normalized)
        return number if number.is_finite() else None
    except InvalidOperation:
        return None
