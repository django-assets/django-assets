"""T6: trades admin + DRF surfaces (spec §6/§7)."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from django_assets.trades.models import Trade

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def admin_client(client):
    admin_user = get_user_model().objects.create_superuser(username="admin", password="x")
    client.force_login(admin_user)
    return client


@pytest.fixture
def trade(user, sale_tx, aapl):
    trade = Trade.objects.create(user=user, name="short AAPL")
    trade.assign(sale_tx, quantity="1000", instrument=aapl)
    trade.add_tag("strategy", "swing")
    return trade


@pytest.mark.parametrize(
    "model_name",
    ["trade", "tradeallocation", "tagcategory", "tag", "virtualtransfer"],
)
def test_changelists_render(admin_client, trade, model_name):
    url = reverse(f"admin:django_assets_{model_name}_changelist")
    assert admin_client.get(url).status_code == 200


def test_trade_change_form_has_inline_and_derived(admin_client, trade):
    response = admin_client.get(
        reverse("admin:django_assets_trade_change", args=[trade.pk])
    )
    body = response.content.decode()
    assert "allocations-TOTAL_FORMS" in body  # allocation inline
    assert "open" in body  # derived status rendered


def test_trade_serializer_unified_pnl(trade):
    from django_assets.serializers import TradeSerializer

    data = TradeSerializer(trade).data
    assert data["status"] == "open"
    assert data["tags"] == {"strategy": ["swing"]}
    assert "realized_pnl" in data
    assert "virtual_pnl" not in data  # unified, ADR-0031


def test_viewset_actions_enforce_rules(user, trade, sale_tx, sale_leg, aapl):
    from rest_framework.test import APIRequestFactory

    from django_assets.viewsets import TradeViewSet

    factory = APIRequestFactory()
    other = Trade.objects.create(user=user, name="other side")

    # assign beyond the partition → 400, not a 500.
    response = TradeViewSet.as_view({"post": "assign"})(
        factory.post(
            f"/trades/{other.pk}/assign/",
            {"transaction": sale_tx.pk, "quantity": "500", "instrument": aapl.pk},
        ),
        pk=other.pk,
    )
    assert response.status_code == 400
    assert "partition" in str(response.data).lower() or "alloc" in str(response.data).lower()

    # transfer_position through the API.
    response = TradeViewSet.as_view({"post": "transfer_position"})(
        factory.post(
            f"/trades/{trade.pk}/transfer-position/",
            {
                "to_trade": other.pk,
                "instrument": aapl.pk,
                "quantity": "100",
                "price": "200.00",
                "timestamp": "2026-03-20T20:00:00Z",
            },
        ),
        pk=trade.pk,
    )
    assert response.status_code == 200
    assert other.net_position(aapl) == D("-100")

    # unassign through the API.
    response = TradeViewSet.as_view({"post": "unassign"})(
        factory.post(f"/trades/{trade.pk}/unassign/", {"transaction": sale_tx.pk}),
        pk=trade.pk,
    )
    assert response.status_code == 200
    assert trade.allocations.count() == 0


def test_no_default_auth_on_trades_viewsets():
    from django_assets import viewsets

    for name in ("TradeViewSet", "VirtualTransferViewSet"):
        cls = getattr(viewsets, name)
        assert "authentication_classes" not in vars(cls)
        assert "permission_classes" not in vars(cls)
