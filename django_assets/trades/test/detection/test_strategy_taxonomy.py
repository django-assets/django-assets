"""Full options-strategy taxonomy (ADR-0037 §3 extended): every
canonical shape classifies to its name; near-misses fall to `mixed`,
never to a dressed-up guess."""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.models import Instrument
from django_assets.instruments.equities.models import EquityMeta
from django_assets.instruments.options.models import OptionMeta
from django_assets.trades.detection import classify_structure

pytestmark = pytest.mark.ledger

D = Decimal
EXP1 = datetime.date(2026, 6, 19)
EXP2 = datetime.date(2026, 9, 18)


class FakeLeg:
    """classify_structure reads instrument_id and amount only."""

    def __init__(self, instrument, amount):
        self.instrument_id = instrument.pk
        self.amount = D(str(amount))


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="taxonomist", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def spy(usd):
    inst = Instrument.objects.create(
        code="SPY", quantity_decimals=4, price_decimals=4, price_currency=usd
    )
    EquityMeta.objects.create(instrument=inst)
    return inst


@pytest.fixture
def option(usd, spy):
    made = {}

    def _option(strike, right, expiry=EXP1):
        key = (strike, right, expiry)
        if key not in made:
            inst = Instrument.objects.create(
                code=f"SPY {expiry:%m/%d/%Y} {strike} {right}",
                quantity_decimals=0,
                price_decimals=4,
                multiplier=D("100"),
                price_currency=usd,
            )
            OptionMeta.objects.create(
                instrument=inst, underlying=spy, expiry=expiry, strike=D(str(strike)), right=right
            )
            made[key] = inst
        return made[key]

    return _option


CASES = [
    # single-leg
    ("long_call", [], [(500, "C", EXP1, 1)]),
    ("long_put", [], [(400, "P", EXP1, 1)]),
    ("short_call", [], [(500, "C", EXP1, -1)]),
    ("cash_secured_put", [], [(400, "P", EXP1, -1)]),
    # verticals, directional
    ("bull_call_spread", [], [(490, "C", EXP1, 1), (510, "C", EXP1, -1)]),
    ("bear_call_spread", [], [(490, "C", EXP1, -1), (510, "C", EXP1, 1)]),
    ("bear_put_spread", [], [(410, "P", EXP1, 1), (390, "P", EXP1, -1)]),
    ("bull_put_spread", [], [(410, "P", EXP1, -1), (390, "P", EXP1, 1)]),
    # ratio family
    ("ratio_call_spread", [], [(490, "C", EXP1, 1), (510, "C", EXP1, -2)]),
    ("ratio_put_spread", [], [(410, "P", EXP1, 1), (390, "P", EXP1, -2)]),
    ("call_backspread", [], [(490, "C", EXP1, -1), (510, "C", EXP1, 2)]),
    ("put_backspread", [], [(410, "P", EXP1, -1), (390, "P", EXP1, 2)]),
    # time spreads
    ("calendar_call_spread", [], [(500, "C", EXP1, -1), (500, "C", EXP2, 1)]),
    ("calendar_put_spread", [], [(400, "P", EXP1, -1), (400, "P", EXP2, 1)]),
    ("diagonal_call_spread", [], [(500, "C", EXP1, -1), (520, "C", EXP2, 1)]),
    ("diagonal_put_spread", [], [(400, "P", EXP1, -1), (380, "P", EXP2, 1)]),
    # volatility pairs
    ("long_straddle", [], [(450, "C", EXP1, 1), (450, "P", EXP1, 1)]),
    ("short_straddle", [], [(450, "C", EXP1, -1), (450, "P", EXP1, -1)]),
    ("long_strangle", [], [(470, "C", EXP1, 1), (430, "P", EXP1, 1)]),
    ("short_strangle", [], [(470, "C", EXP1, -1), (430, "P", EXP1, -1)]),
    # synthetics
    ("long_synthetic_future", [], [(450, "C", EXP1, 1), (450, "P", EXP1, -1)]),
    ("short_synthetic_future", [], [(450, "C", EXP1, -1), (450, "P", EXP1, 1)]),
    ("long_combo", [], [(470, "C", EXP1, 1), (430, "P", EXP1, -1)]),
    ("short_combo", [], [(470, "C", EXP1, -1), (430, "P", EXP1, 1)]),
    # butterflies
    ("long_call_butterfly", [], [(480, "C", EXP1, 1), (500, "C", EXP1, -2), (520, "C", EXP1, 1)]),
    ("short_call_butterfly", [], [(480, "C", EXP1, -1), (500, "C", EXP1, 2), (520, "C", EXP1, -1)]),
    ("long_put_butterfly", [], [(380, "P", EXP1, 1), (400, "P", EXP1, -2), (420, "P", EXP1, 1)]),
    ("call_broken_wing", [], [(480, "C", EXP1, 1), (500, "C", EXP1, -2), (530, "C", EXP1, 1)]),
    ("put_broken_wing", [], [(370, "P", EXP1, 1), (400, "P", EXP1, -2), (420, "P", EXP1, 1)]),
    (
        "inverse_call_broken_wing",
        [],
        [(480, "C", EXP1, -1), (500, "C", EXP1, 2), (530, "C", EXP1, -1)],
    ),
    # three-leg credit
    ("jade_lizard", [], [(430, "P", EXP1, -1), (490, "C", EXP1, -1), (510, "C", EXP1, 1)]),
    # four-leg, one expiry
    (
        "iron_condor",
        [],
        [(430, "P", EXP1, 1), (450, "P", EXP1, -1), (490, "C", EXP1, -1), (510, "C", EXP1, 1)],
    ),
    (
        "reverse_iron_condor",
        [],
        [(430, "P", EXP1, -1), (450, "P", EXP1, 1), (490, "C", EXP1, 1), (510, "C", EXP1, -1)],
    ),
    (
        "iron_butterfly",
        [],
        [(430, "P", EXP1, 1), (470, "P", EXP1, -1), (470, "C", EXP1, -1), (510, "C", EXP1, 1)],
    ),
    (
        "reverse_iron_butterfly",
        [],
        [(430, "P", EXP1, -1), (470, "P", EXP1, 1), (470, "C", EXP1, 1), (510, "C", EXP1, -1)],
    ),
    (
        "box_spread",
        [],
        [(450, "C", EXP1, 1), (450, "P", EXP1, -1), (490, "C", EXP1, -1), (490, "P", EXP1, 1)],
    ),
    (
        "long_call_condor",
        [],
        [(470, "C", EXP1, 1), (490, "C", EXP1, -1), (510, "C", EXP1, -1), (530, "C", EXP1, 1)],
    ),
    (
        "short_put_condor",
        [],
        [(370, "P", EXP1, -1), (390, "P", EXP1, 1), (410, "P", EXP1, 1), (430, "P", EXP1, -1)],
    ),
    # four-leg, two expiries
    (
        "double_calendar",
        [],
        [(430, "P", EXP1, -1), (430, "P", EXP2, 1), (470, "C", EXP1, -1), (470, "C", EXP2, 1)],
    ),
    (
        "double_diagonal",
        [],
        [(430, "P", EXP1, -1), (420, "P", EXP2, 1), (470, "C", EXP1, -1), (480, "C", EXP2, 1)],
    ),
    # in-the-money volatility pairs and same-strike tilts
    ("guts", [], [(430, "C", EXP1, 1), (470, "P", EXP1, 1)]),
    ("short_guts", [], [(430, "C", EXP1, -1), (470, "P", EXP1, -1)]),
    ("strip", [], [(450, "C", EXP1, 1), (450, "P", EXP1, 2)]),
    ("strap", [], [(450, "C", EXP1, 2), (450, "P", EXP1, 1)]),
    # ladders (1:1:1 across three strikes)
    ("bull_call_ladder", [], [(470, "C", EXP1, 1), (490, "C", EXP1, -1), (510, "C", EXP1, -1)]),
    ("bear_call_ladder", [], [(470, "C", EXP1, -1), (490, "C", EXP1, 1), (510, "C", EXP1, 1)]),
    ("bull_put_ladder", [], [(390, "P", EXP1, 1), (410, "P", EXP1, 1), (430, "P", EXP1, -1)]),
    ("bear_put_ladder", [], [(390, "P", EXP1, -1), (410, "P", EXP1, -1), (430, "P", EXP1, 1)]),
    # credit structures
    ("reverse_jade_lizard", [], [(410, "P", EXP1, 1), (430, "P", EXP1, -1), (490, "C", EXP1, -1)]),
    # stock + options
    ("covered_call", [100], [(510, "C", EXP1, -1)]),
    ("protective_put", [100], [(430, "P", EXP1, 1)]),
    ("collar", [100], [(510, "C", EXP1, -1), (430, "P", EXP1, 1)]),
    ("covered_short_strangle", [100], [(510, "C", EXP1, -1), (430, "P", EXP1, -1)]),
    ("covered_short_straddle", [100], [(470, "C", EXP1, -1), (470, "P", EXP1, -1)]),
    ("covered_put", [-100], [(430, "P", EXP1, -1)]),
    ("synthetic_put", [-100], [(510, "C", EXP1, 1)]),
    ("stock", [100], []),
    # honest fallbacks
    ("mixed", [100], [(510, "C", EXP1, -2)]),  # under-covered short calls
    ("mixed", [], [(430, "P", EXP1, -1), (470, "C", EXP2, -1)]),  # strangle across expiries
]


@pytest.mark.parametrize("expected,share_lots,option_specs", CASES)
def test_taxonomy(expected, share_lots, option_specs, spy, option):
    legs = [FakeLeg(spy, qty) for qty in share_lots]
    for strike, right, expiry, count in option_specs:
        legs.append(FakeLeg(option(strike, right, expiry), count))
    assert classify_structure(legs) == expected


def test_cluster_netting_drops_flat_contracts(spy, option):
    call = option(500, "C", EXP1)
    legs = [FakeLeg(call, 2), FakeLeg(call, -2), FakeLeg(spy, 100)]
    assert classify_structure(legs) == "stock"
