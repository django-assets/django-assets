"""ALIVE end-to-end (live, metered): a real multi-leg option position —
a covered call plus a protective put (collar) on SPY — values through
django_assets' Portfolio/valuation surface with real vendor data via the
connector. No mocks anywhere: real ledger rows, real OCC contracts
discovered from today's live chain, real prices and greeks.

Run: uv run pytest connectors/marketdata/verify/live_alive.py -v -s
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django_assets_prices_marketdata.calendar import EASTERN
from django_assets_prices_marketdata.source import MarketDataPriceSource

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Account, Identifier, Instrument
from django_assets.core.prices import OptionQuote, PriceQuote
from django_assets.core.queries import Portfolio
from django_assets.instruments.options.models import OptionMeta

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture(scope="module")
def live_source():
    return MarketDataPriceSource()


def make_option(raw_client, usd, underlying, *, side: str, code_hint: str) -> Instrument:
    chain = raw_client.get(
        "/v1/options/chain/SPY/", {"strikeLimit": "1", "side": side, "dte": "45"}
    )
    symbol = chain["optionSymbol"][0]
    strike = Decimal(chain["strike"][0])
    expiry = datetime.datetime.fromtimestamp(int(chain["expiration"][0]), tz=EASTERN).date()
    right = "C" if side == "call" else "P"
    instrument = Instrument.objects.create(
        code=f"{code_hint} {symbol}",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    OptionMeta.objects.create(
        instrument=instrument, underlying=underlying, expiry=expiry, strike=strike, right=right
    )
    return instrument


def test_collar_position_values_through_the_library(live_source, raw_client):
    from django_assets.core.prices import CachedPriceSource

    # The TTL wrapper pins one consistent snapshot for the whole test, so
    # the sanity recomputation below sees the same quotes Portfolio.value
    # used even while the market moves.
    live_source = CachedPriceSource(live_source, ttl=300)
    usd = Instrument.objects.create(code="USD", quantity_decimals=2, price_decimals=2)
    spy = Instrument.objects.create(
        code="SPY", quantity_decimals=4, price_decimals=2, price_currency=usd
    )
    Identifier.objects.create(instrument=spy, type="ticker", value="SPY", is_active=True)
    call = make_option(raw_client, usd, spy, side="call", code_hint="short")
    put = make_option(raw_client, usd, spy, side="put", code_hint="long")

    user = get_user_model().objects.create_user(username="alive", password="x")
    cash = Account.objects.create(owner=user, name="cash")
    holdings = Account.objects.create(owner=user, name="holdings")
    external = Account.objects.create(owner=user, name="external")

    ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
    with TransactionBuilder(account=cash, timestamp=ts, description="collar") as builder:
        # Long 100 SPY, short 1 call, long 1 put — premiums through cash.
        builder.add_leg(account=holdings, instrument=spy, amount="100")
        builder.add_leg(account=external, instrument=spy, amount="-100")
        builder.add_leg(account=holdings, instrument=call, amount="-1")
        builder.add_leg(account=external, instrument=call, amount="1")
        builder.add_leg(account=holdings, instrument=put, amount="1")
        builder.add_leg(account=external, instrument=put, amount="-1")
        builder.add_leg(account=cash, instrument=usd, amount="-65000.00")
        builder.add_leg(account=external, instrument=usd, amount="65000.00")

    result = Portfolio.value(holdings, live_source)

    assert result.unpriced == [], f"live vendor left positions unpriced: {result.unpriced}"
    assert set(result.totals) == {usd}
    total = result.totals[usd].amount
    assert isinstance(total, D)
    assert total > 0, "long stock collar should be worth something"

    quotes = live_source.get_quotes([spy, call, put])
    print("\n=== ALIVE: real collar valued through django_assets ===")
    for instrument, quote in quotes.items():
        assert isinstance(quote, PriceQuote)
        line = f"{instrument.code:32} {quote.kind.value:9} {quote.price}"
        if isinstance(quote, OptionQuote):
            line += f"  Δ={quote.delta} IV={quote.iv} θ={quote.theta}"
        print(line)
    print(f"{'TOTAL':32} {'':9} {total} USD")

    # The option marks carry live greeks — the phase-2 dashboard's food.
    for leg in (call, put):
        quote = quotes[leg]
        assert isinstance(quote, OptionQuote)
        assert quote.iv is not None and quote.delta is not None

    # Sanity: total = Σ quantized(qty × price × multiplier), exact Decimal —
    # the library's own precision rule (Measure.value / price_decimals).
    expected = (
        spy.quantize_price(D("100") * quotes[spy].price * spy.multiplier)
        + call.quantize_price(D("-1") * quotes[call].price * call.multiplier)
        + put.quantize_price(D("1") * quotes[put].price * put.multiplier)
    )
    assert total == expected
