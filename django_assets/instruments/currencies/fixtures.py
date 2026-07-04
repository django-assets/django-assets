"""Currency seed fixtures (instruments spec §3.1, D-12).

Loaded EXPLICITLY — `manage.py seed_instruments` in the dev project or a
host's own bootstrap. Never auto-seeded into adopter databases. Loading
is idempotent (keyed on iso_code / identifier value).
"""

from django.db import transaction

from django_assets.core.models import Identifier, Instrument
from django_assets.instruments.currencies.models import CurrencyMeta

# (iso_code, iso_numeric, symbol, quantity_decimals, central_bank)
CURRENCIES = [
    ("USD", 840, "$", 2, "Federal Reserve"),
    ("EUR", 978, "€", 2, "European Central Bank"),
    ("GBP", 826, "£", 2, "Bank of England"),
    ("JPY", 392, "¥", 0, "Bank of Japan"),
    ("CHF", 756, "CHF", 2, "Swiss National Bank"),
    ("ARS", 32, "$", 2, "Banco Central de la República Argentina"),
    ("BRL", 986, "R$", 2, "Banco Central do Brasil"),
    ("CAD", 124, "$", 2, "Bank of Canada"),
    ("AUD", 36, "$", 2, "Reserve Bank of Australia"),
    ("CNY", 156, "¥", 2, "People's Bank of China"),
]


def ensure_currency(iso_code: str) -> Instrument:
    """Get-or-create one seeded currency (used by crypto pegs too)."""
    row = next(entry for entry in CURRENCIES if entry[0] == iso_code)
    code, numeric, symbol, decimals, bank = row
    meta = CurrencyMeta.objects.filter(iso_code=code).select_related("instrument").first()
    if meta is not None:
        return meta.instrument
    instrument = Instrument.objects.create(code=code, quantity_decimals=decimals)
    CurrencyMeta.objects.create(
        instrument=instrument,
        iso_code=code,
        iso_numeric=numeric,
        symbol=symbol,
        is_fiat=True,
        central_bank=bank,
    )
    # Global (NULL-exchange), uppercase identifier — ADR-0018 seed convention.
    Identifier.objects.get_or_create(
        type="ticker", value=code, exchange=None, is_active=True, instrument=instrument
    )
    return instrument


def load() -> None:
    with transaction.atomic():
        for code, *_ in CURRENCIES:
            ensure_currency(code)
