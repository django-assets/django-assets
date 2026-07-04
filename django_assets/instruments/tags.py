"""Metadata-tag conventions (ADR-0032 §3/§5/§6) — the write side.

These dicts are the documented contract between instruments' templates
(writers) and the lots rebuild (reader). Lots imports nothing from here
(ADR-0033 DAG); it interprets the shapes. Decimals are carried as
strings; instruments as primary keys.

Tag carriers are Transaction.metadata keys:
- "corporate_action": {"type", "ratio", "instrument_id", [extra]}   (§6)
- "rollover":         {"kind", "option_instrument_id",
                       "underlying_instrument_id", "contracts",
                       "strike", "multiplier"}                      (§3)
- "conversion":       {"from_instrument_id", "to_instrument_id",
                       "from_quantity", "to_quantity"}              (§5)
"""

from decimal import Decimal

from django_assets.core.models import Instrument


def corporate_action_tag(
    type: str,
    instrument: Instrument,
    *,
    ratio: Decimal | None = None,
    **extra: int | str,
) -> dict[str, int | str]:
    tag: dict[str, int | str] = {"type": type, "instrument_id": instrument.pk}
    if ratio is not None:
        tag["ratio"] = str(ratio)
    tag.update(extra)
    return tag


def rollover_tag(
    kind: str,
    *,
    option_instrument: Instrument,
    underlying_instrument: Instrument,
    contracts: Decimal,
    strike: Decimal,
    multiplier: Decimal,
) -> dict[str, int | str]:
    return {
        "kind": kind,  # "exercise" | "assignment"
        "option_instrument_id": option_instrument.pk,
        "underlying_instrument_id": underlying_instrument.pk,
        "contracts": str(contracts),
        "strike": str(strike),
        "multiplier": str(multiplier),
    }


def conversion_tag(
    *,
    from_instrument: Instrument,
    to_instrument: Instrument,
    from_quantity: Decimal,
    to_quantity: Decimal,
) -> dict[str, int | str]:
    return {
        "from_instrument_id": from_instrument.pk,
        "to_instrument_id": to_instrument.pk,
        "from_quantity": str(from_quantity),
        "to_quantity": str(to_quantity),
    }
