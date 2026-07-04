"""T1: Trade + TradeAllocation spine (trades spec §2.1/§2.2, ADR-0030).

The partition rule at both layers: OverAllocationError from the API
pre-check, IntegrityError at COMMIT from the deferred trigger for raw
ORM bypasses. Fractional splits are the ADR-0030 central use case.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction as db_tx
from django.test import override_settings

from django_assets.core.models import Transaction, TransactionLeg
from django_assets.trades.exceptions import OverAllocationError
from django_assets.trades.models import Trade, TradeAllocation

from ..harness import inviolable

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def trade(user):
    return Trade.objects.create(user=user, name="AAPL swing")


def test_name_unique_per_user(user, trade):
    with pytest.raises(IntegrityError), db_tx.atomic():
        Trade.objects.create(user=user, name="AAPL swing")
    other = get_user_model().objects.create_user(username="other", password="x")
    assert Trade.objects.create(user=other, name="AAPL swing").pk  # cross-user OK


def test_fractional_split_golden(user, trade, sale_leg):
    """−1000 AAPL sale split −500/−500, then the fractional −100.1/−899.9."""
    other = Trade.objects.create(user=user, name="AAPL other half")
    with inviolable():
        trade.assign_leg(sale_leg, "-500")
        other.assign_leg(sale_leg, "-500")
    assert trade.net_position(sale_leg.instrument) == D("-500")
    assert other.net_position(sale_leg.instrument) == D("-500")

    TradeAllocation.objects.all().delete()
    trade.assign_leg(sale_leg, "-100.1")
    other.assign_leg(sale_leg, "-899.9")
    assert trade.net_position(sale_leg.instrument) == D("-100.1")
    assert other.net_position(sale_leg.instrument) == D("-899.9")


def test_precheck_raises_over_allocation(trade, sale_leg):
    trade.assign_leg(sale_leg, "-900")
    with pytest.raises(OverAllocationError, match="1000"):
        trade.assign_leg(sale_leg, "-200", category="extra")
    assert TradeAllocation.objects.count() == 1


def test_trigger_backstops_raw_orm_over_allocation(trade, sale_leg):
    """Raw ORM bypass: the deferred trigger raises at COMMIT."""
    with pytest.raises(IntegrityError, match="allocat"), db_tx.atomic():
        TradeAllocation.objects.create(trade=trade, leg=sale_leg, amount=D("-600"))
        TradeAllocation.objects.create(
            trade=trade, leg=sale_leg, amount=D("-600"), category="more"
        )


def test_trigger_rejects_sign_mismatch(trade, sale_leg):
    with pytest.raises(IntegrityError, match="sign"), db_tx.atomic():
        TradeAllocation.objects.create(trade=trade, leg=sale_leg, amount=D("50"))


def test_exact_full_allocation_commits(trade, sale_leg):
    with db_tx.atomic():
        TradeAllocation.objects.create(trade=trade, leg=sale_leg, amount=D("-1000"))
    assert TradeAllocation.objects.count() == 1


def test_delete_never_trips_trigger(trade, sale_leg):
    trade.assign_leg(sale_leg, "-1000")
    with db_tx.atomic():
        TradeAllocation.objects.all().delete()
    assert TradeAllocation.objects.count() == 0


def test_second_session_sees_committed_rows(user, trade, sale_leg):
    """Serialization: after A commits 600, B's 600 exceeds and fails at
    COMMIT (the advisory lock in the trigger orders concurrent commits)."""
    other = Trade.objects.create(user=user, name="B side")
    with db_tx.atomic():
        TradeAllocation.objects.create(trade=trade, leg=sale_leg, amount=D("-600"))
    with pytest.raises(IntegrityError), db_tx.atomic():
        TradeAllocation.objects.create(trade=other, leg=sale_leg, amount=D("-600"))


def test_advisory_lock_present_in_trigger_source():
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            "SELECT prosrc FROM pg_proc WHERE proname = 'assert_trade_allocations_within_leg'"
        )
        source = cur.fetchone()[0]
    assert "pg_advisory_xact_lock" in source


@override_settings(DJANGO_ASSETS_USE_DB_TRIGGERS=False)
def test_precheck_is_sole_enforcement_without_triggers(trade, sale_leg):
    with pytest.raises(OverAllocationError):
        trade.assign_leg(sale_leg, "-1200")


def test_assign_transaction_and_unassign(trade, sale_tx, sale_leg):
    with inviolable():
        trade.assign_transaction(sale_tx)
    assert trade.allocations.count() == 4  # 100% of every leg
    assert trade.net_position(sale_leg.instrument) == D("0")  # both AAPL legs

    trade.unassign(sale_tx)
    assert trade.allocations.count() == 0
    assert Transaction.objects.count() == 1  # core untouched


def test_reallocate_respects_partition(user, trade, sale_leg):
    other = Trade.objects.create(user=user, name="other")
    trade.assign_leg(sale_leg, "-400")
    other.assign_leg(sale_leg, "-600")
    with pytest.raises(OverAllocationError):
        trade.reallocate(sale_leg, "-500")
    trade.reallocate(sale_leg, "-300")
    assert trade.allocations.get(leg=sale_leg).amount == D("-300")


def test_unique_trade_leg_category(trade, sale_leg):
    trade.assign_leg(sale_leg, "-100")
    with pytest.raises(IntegrityError), db_tx.atomic():
        TradeAllocation.objects.create(trade=trade, leg=sale_leg, amount=D("-100"))


def test_deletion_directions(user, trade, sale_tx, sale_leg):
    trade.assign_leg(sale_leg, "-100")
    trade.delete()  # removes allocations, never core rows
    assert TradeAllocation.objects.count() == 0
    assert TransactionLeg.objects.count() == 4

    trade2 = Trade.objects.create(user=user, name="again")
    trade2.assign_leg(sale_leg, "-100")
    sale_tx.delete()  # core cascade takes the allocations with the legs
    assert TradeAllocation.objects.count() == 0


def test_float_rejected(trade, sale_leg):
    with pytest.raises(TypeError, match="Decimal"):
        trade.assign_leg(sale_leg, -100.5)  # float-ok
