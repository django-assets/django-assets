"""L4: wash sales (lots spec §2.5, ADR-0032 §7, D-37)."""

from decimal import Decimal

import pytest

from django_assets.lots.models import WashSaleAdjustment
from django_assets.lots.rebuild import rebuild_lots

from ..conftest import at

pytestmark = pytest.mark.ledger

D = Decimal


def test_wash_sale_within_window(accounts, aapl, buy, sell):
    """Loss sale with a repurchase 10 days later: loss disallowed,
    recorded as a basis addition on the replacement lot; the original
    match rows stay untouched."""
    buy("100", "50.00", at(0))
    sell("100", "40.00", at(100))  # −1000 loss
    buy("100", "42.00", at(110))  # replacement inside 30 days
    rebuild_lots(accounts["holdings"])

    adjustment = WashSaleAdjustment.objects.get()
    assert adjustment.disallowed_loss == D("1000.00")
    assert adjustment.replacement_lot.acquired_at == at(110)
    loss_match = adjustment.loss_match
    assert loss_match.realized_gain == D("-1000.00")  # match untouched


def test_no_adjustment_outside_window(accounts, aapl, buy, sell):
    buy("100", "50.00", at(0))
    sell("100", "40.00", at(100))
    buy("100", "42.00", at(140))  # 40 days later: clean loss
    rebuild_lots(accounts["holdings"])
    assert not WashSaleAdjustment.objects.exists()


def test_partial_replacement_prorates(accounts, aapl, buy, sell):
    buy("100", "50.00", at(0))
    sell("100", "40.00", at(100))  # −1000 loss
    buy("40", "42.00", at(105))  # only 40 shares replaced
    rebuild_lots(accounts["holdings"])
    adjustment = WashSaleAdjustment.objects.get()
    assert adjustment.disallowed_loss == D("400.00")  # 40% of the loss


def test_gains_never_wash(accounts, aapl, buy, sell):
    buy("100", "50.00", at(0))
    sell("100", "60.00", at(100))  # gain
    buy("100", "58.00", at(105))
    rebuild_lots(accounts["holdings"])
    assert not WashSaleAdjustment.objects.exists()


def test_rebuild_idempotent_with_adjustments(accounts, aapl, buy, sell):
    buy("100", "50.00", at(0))
    sell("100", "40.00", at(100))
    buy("100", "42.00", at(110))
    rebuild_lots(accounts["holdings"])
    first = list(WashSaleAdjustment.objects.values_list("disallowed_loss", flat=True))
    rebuild_lots(accounts["holdings"])
    second = list(WashSaleAdjustment.objects.values_list("disallowed_loss", flat=True))
    assert first == second == [D("1000.00")]
