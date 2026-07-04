"""L1: lot construction, rebuild determinism, staleness, corporate
actions, and the conservation trigger (lots spec §2.1/§2.3, ADR-0032)."""

from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.db import transaction as db_tx
from django.test import override_settings

from django_assets.lots.models import Lot, LotEvent, StaleLotScope
from django_assets.lots.rebuild import rebuild_lots

from ..conftest import at

pytestmark = pytest.mark.ledger

D = Decimal


def test_acquisition_builds_lot_with_capitalized_basis(accounts, aapl, buy):
    """US convention: commission capitalizes into basis; acquired_at
    falls back from trade_timestamp to timestamp."""
    trade_ts = at(0, hours=-5)
    buy("100", "10.00", at(0), commission="1.00", trade_timestamp=trade_ts)
    rebuild_lots(accounts["holdings"])
    lot = Lot.objects.get()
    assert lot.quantity == D("100")
    assert lot.cost_basis == D("1001.00")  # 1000 principal + 1 commission
    assert lot.quantity_remaining == D("100")
    assert lot.cost_basis_remaining == D("1001.00")
    assert lot.direction == "long"
    assert lot.acquired_at == trade_ts


def test_rebuild_is_deterministic(accounts, aapl, buy, sell):
    buy("100", "10.00", at(0))
    buy("50", "12.00", at(1))
    sell("120", "15.00", at(2))
    rebuild_lots(accounts["holdings"])
    first = list(
        Lot.objects.order_by("id").values_list(
            "quantity", "quantity_remaining", "cost_basis", "cost_basis_remaining"
        )
    )
    rebuild_lots(accounts["holdings"])
    second = list(
        Lot.objects.order_by("id").values_list(
            "quantity", "quantity_remaining", "cost_basis", "cost_basis_remaining"
        )
    )
    assert first == second


def test_conservation_trigger_backstops_raw_writes(accounts, aapl, buy):
    buy("100", "10.00", at(0))
    rebuild_lots(accounts["holdings"])
    lot = Lot.objects.get()
    with pytest.raises(IntegrityError, match="onserv"), db_tx.atomic():
        lot.quantity_remaining = D("50")  # no matches back this up
        lot.save()


def test_rebuild_commits_clean_under_trigger(accounts, aapl, buy, sell):
    """Truncate-and-rewrite is consistent at COMMIT — no bypass needed."""
    buy("100", "10.00", at(0))
    sell("40", "11.00", at(5))
    with db_tx.atomic():
        rebuild_lots(accounts["holdings"])
    lot = Lot.objects.get()
    assert lot.quantity_remaining == D("60")


@override_settings(DJANGO_ASSETS_USE_DB_TRIGGERS=False)
def test_assertion_fallback_without_triggers(accounts, aapl, buy, monkeypatch):
    """Fault injection: a corrupted rebuild trips the app assertion."""
    import django_assets.lots.rebuild as rebuild_module

    buy("100", "10.00", at(0))

    original = rebuild_module._build_scope

    def corrupt(*args, **kwargs):
        lots = original(*args, **kwargs)
        for lot in Lot.objects.all():
            Lot.objects.filter(pk=lot.pk).update(quantity_remaining=D("1"))
        return lots

    rebuild_lots(accounts["holdings"])  # clean pass first
    monkeypatch.setattr(rebuild_module, "_build_scope", corrupt)
    with pytest.raises(AssertionError, match="onserv"):
        rebuild_lots(accounts["holdings"])


def test_staleness_marks_pair_and_auto_rebuild(accounts, aapl, usd, buy):
    from django_assets.lots.queries import open_lots

    buy("100", "10.00", at(0))
    assert StaleLotScope.objects.filter(
        account=accounts["holdings"], instrument=aapl
    ).exists()
    rows = open_lots(accounts["holdings"])  # auto-rebuild on query
    assert len(rows) == 1
    assert not StaleLotScope.objects.filter(
        account=accounts["holdings"], instrument=aapl
    ).exists()

    buy("10", "11.00", at(1))  # ledger edit re-marks exactly the pair
    stale = StaleLotScope.objects.filter(account=accounts["holdings"])
    assert {scope.instrument_id for scope in stale} == {aapl.pk}
    assert len(open_lots(accounts["holdings"])) == 2


def test_tagged_split_adjusts_lots_with_events(accounts, aapl, buy):
    """ADR-0032 §6: a tagged 4:1 split quadruples quantity, quarters
    per-share basis, and writes reconciling LotEvent rows; untagged
    transactions never trigger adjustments."""
    from django_assets.instruments.equities import templates as eq

    buy("100", "40.00", at(0))
    eq.stock_split(
        accounts=accounts,
        instrument=aapl,
        additional_quantity="300",
        ratio="4",
        timestamp=at(10),
    )
    rebuild_lots(accounts["holdings"])
    lot = Lot.objects.get()
    assert lot.quantity_remaining == D("400")
    assert lot.cost_basis == D("4000.00")  # unchanged total
    event = LotEvent.objects.get()
    assert event.event_type == "split"
    assert event.quantity_before == D("100")
    assert event.quantity_after == D("400")
    assert event.basis_before == event.basis_after == D("4000.00")


def test_inviolability(accounts, aapl, buy):
    from django_assets.trades.test.harness import inviolable

    buy("100", "10.00", at(0))
    with inviolable():
        rebuild_lots(accounts["holdings"])
