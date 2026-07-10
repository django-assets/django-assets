"""Instrument → MarketData symbol, via Identifier (ADR-0009/0034).

Equities map through an active `ticker` identifier. Options map through
an active `opra` identifier when one exists, else the OCC symbol is
synthesized from OptionMeta (underlying's ticker + yymmdd + C/P +
strike in mills, 8 digits) — the same symbology MarketData speaks.
Unmappable → None, never guessed: no identifier, no option metadata, a
non-USD price currency, or a strike that doesn't fit OCC encoding all
mean "this source cannot price this instrument".
"""

from dataclasses import dataclass
from decimal import Decimal

from django_assets.core.models import Instrument
from django_assets.instruments.options.models import OptionMeta

DEFAULT_CURRENCY_CODES = ("USD",)


@dataclass(frozen=True)
class VendorSymbol:
    symbol: str
    is_option: bool


def _active_identifier(instrument: Instrument, type_: str) -> str | None:
    values = list(
        instrument.identifiers.filter(type=type_, is_active=True)
        .order_by("id")
        .values_list("value", flat=True)[:1]
    )
    return values[0] if values else None


def _occ_symbol(meta: OptionMeta, root: str) -> str | None:
    mills = meta.strike * Decimal(1000)
    if mills != mills.to_integral_value() or not (0 < mills < Decimal(10**8)):
        return None  # strike doesn't fit OCC's 8-digit mills field
    return f"{root}{meta.expiry:%y%m%d}{meta.right}{int(mills):08d}"


def map_instrument(
    instrument: Instrument,
    *,
    currency_codes: tuple[str, ...] = DEFAULT_CURRENCY_CODES,
) -> VendorSymbol | None:
    """The vendor symbol for an instrument, or None = unpriceable here."""
    currency = instrument.price_currency
    if currency is None or currency.code not in currency_codes:
        return None  # MarketData serves USD markets; anything else would lie

    meta = OptionMeta.objects.filter(instrument=instrument).select_related("underlying").first()
    if meta is not None:
        opra = _active_identifier(instrument, "opra")
        if opra:
            return VendorSymbol(symbol=opra, is_option=True)
        root = _active_identifier(meta.underlying, "ticker")
        if not root:
            return None
        occ = _occ_symbol(meta, root)
        return VendorSymbol(symbol=occ, is_option=True) if occ else None

    ticker = _active_identifier(instrument, "ticker")
    if ticker:
        return VendorSymbol(symbol=ticker, is_option=False)
    return None
