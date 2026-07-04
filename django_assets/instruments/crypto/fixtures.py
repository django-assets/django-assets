"""Crypto seed fixtures (instruments spec §3.2, D-12).

Explicit loading only, idempotent. Stablecoins peg to the USD
Instrument; because type packages never import each other (ADR-0033),
the peg target is found — or minimally created — through core lookups
only. Run the currencies fixtures first (seed_instruments does) to get
the fully-described USD row.
"""

from django.db import transaction

from django_assets.core.models import Identifier, Instrument
from django_assets.instruments.crypto.models import CryptoMeta

# (symbol, network, quantity_decimals, is_stablecoin, pegged_to_code)
CRYPTOS = [
    ("BTC", "bitcoin", 8, False, None),
    ("ETH", "ethereum", 18, False, None),
    ("USDC", "ethereum", 6, True, "USD"),
    ("USDT", "ethereum", 6, True, "USD"),
    ("DAI", "ethereum", 18, True, "USD"),
    ("SOL", "solana", 9, False, None),
    ("DOGE", "dogecoin", 8, False, None),
]


def _peg_target(code: str) -> Instrument:
    """Find or create the pegged currency through CORE lookups only —
    the sideways import ban (ADR-0033) keeps currencies/ out of here."""
    existing = Instrument.objects.filter(
        identifiers__type="ticker", identifiers__value=code, identifiers__is_active=True
    ).first()
    if existing is not None:
        return existing
    instrument = Instrument.objects.create(code=code, quantity_decimals=2)
    Identifier.objects.create(type="ticker", value=code, exchange=None, instrument=instrument)
    return instrument


def load() -> None:
    with transaction.atomic():
        for symbol, network, decimals, is_stable, peg_code in CRYPTOS:
            if CryptoMeta.objects.filter(symbol=symbol).exists():
                continue
            instrument = Instrument.objects.create(code=symbol, quantity_decimals=decimals)
            CryptoMeta.objects.create(
                instrument=instrument,
                symbol=symbol,
                network=network,
                is_stablecoin=is_stable,
                pegged_to=_peg_target(peg_code) if peg_code else None,
            )
            Identifier.objects.get_or_create(
                type="ticker",
                value=symbol,
                exchange=None,
                is_active=True,
                instrument=instrument,
            )
