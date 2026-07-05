"""L3: exercise/assignment rollover (ADR-0032 §3, lots spec §2.1)."""

import datetime
from decimal import Decimal

import pytest

from django_assets.core.models import Instrument
from django_assets.instruments.options import templates as opt
from django_assets.instruments.options.models import Deliverable, OptionMeta
from django_assets.lots.models import ExerciseLink, Lot, LotMatch
from django_assets.lots.rebuild import rebuild_lots

from ..conftest import at

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def xyz(usd):
    return Instrument.objects.create(
        code="XYZ", quantity_decimals=0, price_decimals=4, price_currency=usd
    )


@pytest.fixture
def put(usd, xyz):
    option = Instrument.objects.create(
        code="XYZ P10",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    meta = OptionMeta.objects.create(
        instrument=option,
        underlying=xyz,
        expiry=datetime.date(2026, 12, 18),
        strike=D("10"),
        right="P",
    )
    Deliverable.objects.create(
        option_meta=meta,
        instrument=xyz,
        quantity=D("100"),
        effective_from=datetime.date(2026, 1, 2),
    )
    return option


def test_linked_assignment_rolls_premium_into_basis(accounts, usd, xyz, put):
    """Short $10 put for $0.50 premium, assigned ⇒ shares at $9.50/sh;
    the option roundtrip is no standalone result — automatic via the
    tag the assignment template wrote."""
    opt.sell_option(accounts=accounts, instrument=put, contracts="1", price="0.50", timestamp=at(0))
    opt.assign_option(accounts=accounts, instrument=put, contracts="1", timestamp=at(10))
    rebuild_lots(accounts["holdings"])

    assert ExerciseLink.objects.filter(source="metadata").exists()
    share_lot = Lot.objects.get(instrument=xyz)
    assert share_lot.cost_basis == D("950.00")  # 1000 strike − 50 premium
    assert share_lot.rollover_linked is True
    # No standalone option gain: the put lot's match nets to zero result.
    option_matches = LotMatch.objects.filter(lot__instrument=put)
    assert sum(m.realized_gain for m in option_matches) == D("0")


def test_linked_exercise_call_basis_is_strike_plus_premium(accounts, usd, xyz):
    call = Instrument.objects.create(
        code="XYZ C10",
        quantity_decimals=0,
        price_decimals=4,
        multiplier=D("100"),
        price_currency=usd,
    )
    meta = OptionMeta.objects.create(
        instrument=call,
        underlying=xyz,
        expiry=datetime.date(2026, 12, 18),
        strike=D("10"),
        right="C",
    )
    Deliverable.objects.create(
        option_meta=meta,
        instrument=xyz,
        quantity=D("100"),
        effective_from=datetime.date(2026, 1, 2),
    )
    opt.buy_option(accounts=accounts, instrument=call, contracts="1", price="0.75", timestamp=at(0))
    opt.exercise_option(accounts=accounts, instrument=call, contracts="1", timestamp=at(5))
    rebuild_lots(accounts["holdings"])
    share_lot = Lot.objects.get(instrument=xyz)
    assert share_lot.cost_basis == D("1075.00")  # 1000 strike + 75 premium
    assert share_lot.rollover_linked is True


def test_unlinked_exercise_strike_only_basis(accounts, usd, xyz, put):
    """Imported history without tags: strike-only basis, premium stands
    alone, and the lot is flagged unlinked in reports."""
    opt.sell_option(accounts=accounts, instrument=put, contracts="1", price="0.50", timestamp=at(0))
    assignment = opt.assign_option(
        accounts=accounts, instrument=put, contracts="1", timestamp=at(10)
    )
    assignment.metadata = {}  # simulate tag-less import
    assignment.save(update_fields=["metadata"])
    rebuild_lots(accounts["holdings"])

    share_lot = Lot.objects.get(instrument=xyz)
    assert share_lot.cost_basis == D("1000.00")  # strike only
    assert share_lot.rollover_linked is False
    option_gain = sum(
        m.realized_gain for m in LotMatch.objects.filter(lot__instrument=put)
    )
    assert option_gain == D("50.00")  # premium stands alone


def test_manual_linkage_api_wins_and_reverses(accounts, usd, xyz, put):
    from django_assets.lots.links import link_exercise, unlink_exercise
    from django_assets.lots.models import StaleLotScope
    from django_assets.lots.queries import open_lots

    opt.sell_option(accounts=accounts, instrument=put, contracts="1", price="0.50", timestamp=at(0))
    assignment = opt.assign_option(
        accounts=accounts, instrument=put, contracts="1", timestamp=at(10)
    )
    assignment.metadata = {}
    assignment.save(update_fields=["metadata"])
    rebuild_lots(accounts["holdings"])
    assert Lot.objects.get(instrument=xyz).rollover_linked is False

    delivered_leg = assignment.legs.get(instrument=xyz, account=accounts["holdings"])
    link_exercise(assignment, delivered_leg, option_instrument=put)  # manual
    assert StaleLotScope.objects.filter(account=accounts["holdings"]).exists()
    lots = open_lots(accounts["holdings"], xyz)  # auto-rebuild applies it
    assert lots[0].cost_basis == D("950.00")
    assert lots[0].rollover_linked is True

    unlink_exercise(assignment, delivered_leg)
    assert open_lots(accounts["holdings"], xyz)[0].cost_basis == D("1000.00")
