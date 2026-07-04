"""Fixtures for the option lifecycle tests (I3) — the PFE1 world.

OCC memo #47935: the Pfizer/Viatris spinoff adjusted all open PFE
options on 2020-11-17 so each contract thereafter delivers
100 PFE + 12 VTRS + $6.47 cash.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.models import Account, Instrument
from django_assets.instruments.models import CorporateAction
from django_assets.instruments.options.models import Deliverable, OptionMeta

D = Decimal
CUTOVER = datetime.date(2020, 11, 17)
LISTED = datetime.date(2020, 1, 2)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def pfe(usd):
    return Instrument.objects.create(
        code="PFE", quantity_decimals=0, price_decimals=4, price_currency=usd
    )


@pytest.fixture
def vtrs(usd):
    return Instrument.objects.create(
        code="VTRS", quantity_decimals=0, price_decimals=4, price_currency=usd
    )


@pytest.fixture
def pfe1_call(usd, pfe, vtrs):
    """PFE1 Dec 2020 $35 call with the pre/post-spinoff deliverable regimes."""
    option = Instrument.objects.create(
        code="PFE1 201218C00035000",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    meta = OptionMeta.objects.create(
        instrument=option,
        underlying=pfe,
        expiry=datetime.date(2020, 12, 18),
        strike=D("35"),
        right="C",
    )
    action = CorporateAction.objects.create(
        effective_date=CUTOVER,
        action_type="spinoff",
        source_reference="OCC #47935",
        primary_instrument=pfe,
    )
    # Pre-spinoff: plain 100 PFE, [LISTED, CUTOVER).
    Deliverable.objects.create(
        option_meta=meta,
        sequence=0,
        instrument=pfe,
        quantity=D("100"),
        effective_from=LISTED,
        effective_to=CUTOVER,
    )
    # Post-spinoff: 100 PFE + 12 VTRS + $6.47, [CUTOVER, ∞).
    Deliverable.objects.create(
        option_meta=meta,
        sequence=0,
        instrument=pfe,
        quantity=D("100"),
        effective_from=CUTOVER,
        corporate_action=action,
    )
    Deliverable.objects.create(
        option_meta=meta,
        sequence=1,
        instrument=vtrs,
        quantity=D("12"),
        effective_from=CUTOVER,
        corporate_action=action,
    )
    Deliverable.objects.create(
        option_meta=meta,
        sequence=2,
        cash_currency=usd,
        cash_amount=D("6.47"),
        effective_from=CUTOVER,
        corporate_action=action,
    )
    return option


@pytest.fixture
def accounts(user):
    names = ["cash", "holdings", "external", "commissions", "regulatory_fees"]
    return {n: Account.objects.create(owner=user, name=n) for n in names}


def legs_by(tx):
    result = {}
    for leg in tx.legs.select_related("account", "instrument"):
        key = (leg.account.name, leg.instrument.code)
        assert key not in result, f"duplicate leg {key}"
        result[key] = leg.amount
    return result


def ts(year, month, day, hour=15):
    return datetime.datetime(year, month, day, hour, tzinfo=datetime.UTC)
