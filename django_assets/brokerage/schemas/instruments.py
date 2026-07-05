"""Importer-owned reference-data helpers (ADR-0018's sanctioned pattern:
instrument creation is a deliberate import job, never a lookup side
effect — so it lives HERE, not in the resolver).

Shared by the built-in broker schemas: idempotent ensure_* helpers keyed
on uppercase global identifiers, plus the OCC-style option parsers.
"""

import datetime
import re
from decimal import Decimal

from django_assets.core.exceptions import InstrumentNotFoundError
from django_assets.core.models import Identifier, Instrument
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.options.models import OptionMeta

#: "MSTR 01/16/2026 800.00 C" — Schwab's option symbol column.
SCHWAB_OPTION_SYMBOL = re.compile(
    r"^(?P<underlying>[A-Z][A-Z0-9./]*)\s+"
    r"(?P<expiry>\d{2}/\d{2}/\d{4})\s+"
    r"(?P<strike>\d+(?:\.\d+)?)\s+"
    r"(?P<right>[CP])$"
)

#: "PSTH 4/23/2021 Call $30.00" — Robinhood's option description.
ROBINHOOD_OPTION_DESC = re.compile(
    r"(?P<underlying>[A-Z][A-Z0-9./]*)\s+"
    r"(?P<expiry>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<right>Call|Put)\s+\$(?P<strike>\d+(?:\.\d+)?)"
)


def parse_money(value: str) -> Decimal:
    """Broker money: '$1,234.56', '-$9.16', '($5.00)' → signed Decimal."""
    text = value.strip()
    if not text or text == "-":  # dash = empty cell on statement PDFs
        return Decimal(0)
    text = text.replace("$", "").replace(",", "").strip()  # "$(1.23)" renders too
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    amount = Decimal(text)
    return -amount if negative else amount


def parse_us_date(value: str) -> datetime.date:
    month, day, year = value.strip().split("/")
    return datetime.date(int(year), int(month), int(day))


def _find(symbol: str) -> Instrument | None:
    try:
        return Instrument.resolve(symbol)
    except InstrumentNotFoundError:
        return None


def ensure_currency(code: str) -> Instrument:
    existing = _find(code)
    if existing is not None:
        return existing
    instrument = Instrument.objects.create(code=code, quantity_decimals=2)
    Identifier.objects.create(type="ticker", value=code, instrument=instrument)
    return instrument


def ensure_equity(symbol: str, *, currency: Instrument) -> Instrument:
    """Equity/ETF/received-security row. Fractional-share brokers exist,
    so imported equities carry 8 quantity decimals."""
    existing = _find(symbol)
    if existing is not None:
        return existing
    instrument = Instrument.objects.create(
        code=symbol, quantity_decimals=8, price_decimals=4, price_currency=currency
    )
    Identifier.objects.create(type="ticker", value=symbol, instrument=instrument)
    EquityMeta.objects.create(instrument=instrument)
    return instrument


def ensure_option(
    *,
    underlying_symbol: str,
    expiry: datetime.date,
    strike: Decimal,
    right: str,
    currency: Instrument,
) -> Instrument:
    code = f"{underlying_symbol} {expiry:%m/%d/%Y} {strike.normalize():f} {right}"
    existing = _find(code)
    if existing is not None:
        return existing
    underlying = ensure_equity(underlying_symbol, currency=currency)
    instrument = Instrument.objects.create(
        code=code,
        quantity_decimals=0,
        price_decimals=4,
        multiplier=Decimal(100),
        price_currency=currency,
    )
    Identifier.objects.create(type="ticker", value=code, instrument=instrument)
    OptionMeta.objects.create(
        instrument=instrument,
        underlying=underlying,
        expiry=expiry,
        strike=strike,
        right=right,
    )
    return instrument


def option_from_schwab_symbol(symbol: str, *, currency: Instrument) -> Instrument | None:
    match = SCHWAB_OPTION_SYMBOL.match(symbol.strip())
    if match is None:
        return None
    return ensure_option(
        underlying_symbol=match["underlying"],
        expiry=parse_us_date(match["expiry"]),
        strike=Decimal(match["strike"]),
        right=match["right"],
        currency=currency,
    )


def option_from_robinhood_description(
    description: str, *, currency: Instrument
) -> Instrument | None:
    match = ROBINHOOD_OPTION_DESC.search(description)
    if match is None:
        return None
    return ensure_option(
        underlying_symbol=match["underlying"],
        expiry=parse_us_date(match["expiry"]),
        strike=Decimal(match["strike"]),
        right="C" if match["right"] == "Call" else "P",
        currency=currency,
    )
