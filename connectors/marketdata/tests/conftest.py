"""Shared scenario for connector tests.

Frozen clock: Friday 2026-07-10 15:00 ET (19:00 UTC) — market OPEN,
last completed session Thursday 2026-07-09, with 2026-07-03 a holiday.
The FakeVendor serves ACME (a stock listed 2024-06-03) and one ACME
call option, with quote timestamps derived from the frozen now so
entitlement probes classify deterministically:

- delayed quote updated 15 minutes ago  → stocks delayed entitlement
- realtime price updated 10 seconds ago → stocks realtime confirmed
- permissions header 'delayed_quotes_permission,…' → options delayed
"""

import datetime

import pytest
from django_assets_prices_marketdata.client import MarketDataClient
from django_assets_prices_marketdata.source import MarketDataPriceSource

from django_assets.core.models import Identifier, Instrument
from django_assets.instruments.options.models import OptionMeta

from .fake_vendor import EASTERN, FakeVendor, epoch, make_daily_candles

NOW_UTC = datetime.datetime(2026, 7, 10, 19, 0, tzinfo=datetime.UTC)  # 15:00 ET Friday
HOLIDAYS = (datetime.date(2026, 7, 3),)
LISTED = datetime.date(2024, 6, 3)
LAST_SESSION = datetime.date(2026, 7, 9)
OPTION_SYMBOL = "ACME260807C00752000"

FROZEN = "2026-07-10 19:00:00"


def eastern(when: str) -> int:
    return epoch(datetime.datetime.fromisoformat(when).replace(tzinfo=EASTERN))


@pytest.fixture
def vendor():
    candles = make_daily_candles(LISTED, LAST_SESSION, holidays=HOLIDAYS)
    series_sessions = [
        datetime.date(2026, 7, 1),
        datetime.date(2026, 7, 2),
        datetime.date(2026, 7, 6),
        datetime.date(2026, 7, 7),
        datetime.date(2026, 7, 8),
        datetime.date(2026, 7, 9),
    ]
    option_series = []
    for index, session in enumerate(series_sessions):
        mark = f"{10 + index}.{25 + index}"
        option_series.append(
            {
                "session": session,
                "updated": epoch(
                    datetime.datetime.combine(session, datetime.time(20, 0), tzinfo=EASTERN)
                ),
                "bid": mark,
                "ask": mark,
                "mid": mark,
                "last": mark,
                "iv": "0.1363",
                "delta": "0.524",
                "gamma": "0.012",
                "theta": "-0.135",
                "vega": "0.842",
                "underlyingPrice": "751.71",
                "openInterest": 3400 + index,
                "volume": 120 + index,
            }
        )
    option_live = {
        "session": datetime.date(2026, 7, 10),
        "updated": epoch(NOW_UTC - datetime.timedelta(minutes=16)),
        "bid": "12.05",
        "ask": "12.17",
        "mid": "12.11",
        "last": "12.10",
        "iv": "0.1401",
        "delta": "0.531",
        "gamma": "0.012",
        "theta": "-0.133",
        "vega": "0.844",
        "underlyingPrice": "752.40",
        "openInterest": 3406,
        "volume": 57,
    }
    return FakeVendor(
        stocks={"ACME": candles},
        stock_quotes={
            "ACME": {
                "bid": "751.40",
                "ask": "751.44",
                "mid": "751.42",
                "last": "751.71",
                "updated": epoch(NOW_UTC - datetime.timedelta(minutes=15)),
            }
        },
        stock_prices={
            "ACME": {"mid": "751.463", "updated": epoch(NOW_UTC - datetime.timedelta(seconds=10))}
        },
        option_series={OPTION_SYMBOL: option_series},
        option_live={OPTION_SYMBOL: option_live},
        holidays=HOLIDAYS,
    )


@pytest.fixture
def source(vendor):
    client = MarketDataClient(token="test-token", transport=vendor.transport())
    return MarketDataPriceSource(client=client, probe_symbol="ACME")


@pytest.fixture
def usd(db):
    return Instrument.objects.create(code="USD", quantity_decimals=2, price_decimals=2)


@pytest.fixture
def acme(usd):
    instrument = Instrument.objects.create(
        code="ACME", quantity_decimals=0, price_decimals=2, price_currency=usd
    )
    Identifier.objects.create(instrument=instrument, type="ticker", value="ACME", is_active=True)
    return instrument


@pytest.fixture
def acme_call(usd, acme):
    from decimal import Decimal

    instrument = Instrument.objects.create(
        code="ACME 08/07/26 C752",
        quantity_decimals=0,
        price_decimals=2,
        multiplier=Decimal("100"),
        price_currency=usd,
    )
    OptionMeta.objects.create(
        instrument=instrument,
        underlying=acme,
        expiry=datetime.date(2026, 8, 7),
        strike=Decimal("752"),
        right="C",
    )
    return instrument
