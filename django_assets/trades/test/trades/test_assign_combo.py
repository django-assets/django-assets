"""Multi-leg combo assignment: the user/mirror split must judge role by
CASH coherence only — sibling asset legs of a combo (spread fills) must
not masquerade as cash and flip a leg to the counterparty side."""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import Instrument
from django_assets.trades.models import Trade

pytestmark = pytest.mark.django_db

D = Decimal
TS = datetime.datetime(2026, 7, 1, 14, 0, tzinfo=datetime.UTC)


def test_assign_combo_keeps_user_side_of_every_pair(user, usd, accounts):
    short_leg_inst = Instrument.objects.create(
        code="SPY P190", quantity_decimals=0, multiplier=D("100"), price_currency=usd
    )
    long_leg_inst = Instrument.objects.create(
        code="SPY P185", quantity_decimals=0, multiplier=D("100"), price_currency=usd
    )
    with TransactionBuilder(account=accounts["cash"], timestamp=TS, description="combo") as b:
        b.add_leg(account=accounts["holdings"], instrument=short_leg_inst, amount="-5")
        b.add_leg(account=accounts["market"], instrument=short_leg_inst, amount="5")
        b.add_leg(account=accounts["holdings"], instrument=long_leg_inst, amount="5")
        b.add_leg(account=accounts["market"], instrument=long_leg_inst, amount="-5")
        b.add_leg(account=accounts["cash"], instrument=usd, amount="450.25")
        b.add_leg(account=accounts["market"], instrument=usd, amount="-450.25")
        b.add_leg(account=accounts["cash"], instrument=usd, amount="-6.50")
        b.add_leg(account=accounts["market"], instrument=usd, amount="6.50")

    trade = Trade.objects.create(user=user, name="combo")
    trade.assign(b.transaction, fraction=1)

    assert trade.net_position(short_leg_inst) == D("-5")
    assert trade.net_position(long_leg_inst) == D("5")
    by_category = {}
    for allocation in trade.allocations.select_related("leg__account"):
        by_category.setdefault(allocation.category, []).append(allocation)
    assert all(a.leg.account == accounts["holdings"] for a in by_category[""])
    assert [a.amount for a in by_category["revenue"]] == [D("450.25")]
    assert [a.amount for a in by_category["fee"]] == [D("-6.50")]
