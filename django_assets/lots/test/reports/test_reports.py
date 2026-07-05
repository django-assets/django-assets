"""L5: reports + surfaces (lots spec plan L5)."""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from django_assets.core.prices import StaticPriceSource
from django_assets.core.queries import Portfolio
from django_assets.lots.reports import open_lots_report, realized_gains, unrealized

from ..conftest import at

pytestmark = pytest.mark.ledger

D = Decimal


def test_realized_gains_1099_rows(accounts, aapl, buy, sell):
    buy("100", "50.00", at(0))
    sell("60", "40.00", at(100))  # short-term loss
    buy("60", "42.00", at(110))  # wash replacement
    sell("40", "70.00", at(400))  # long-term gain
    rows = realized_gains(accounts["holdings"])  # auto-rebuild applies
    assert len(rows) == 2
    loss, gain = rows
    assert loss["term"] == "short"
    assert loss["wash_sale_disallowed"] == D("600.00")
    assert gain["term"] == "long"
    assert gain["realized_gain"] == D("800.00")
    assert not loss["unlinked"]


def test_open_lots_reconciles_with_portfolio(accounts, aapl, buy, sell):
    buy("100", "50.00", at(0))
    sell("30", "55.00", at(10))
    rows = open_lots_report(accounts["holdings"])
    total = sum(row["quantity_remaining"] for row in rows)
    assert total == Portfolio.at(accounts["holdings"])[aapl]


def test_unrealized_with_price_source(accounts, aapl, buy):
    buy("100", "50.00", at(0), commission="10.00")
    result = unrealized(accounts["holdings"], StaticPriceSource({aapl: "60.00"}))
    assert result["unrealized_gain"] == D("990.00")  # 6000 − 5010
    assert result["unpriced"] == []


def test_admin_read_mostly_and_command(accounts, aapl, buy, client):
    buy("100", "50.00", at(0))
    call_command("rebuild_lots")  # operational command path
    from django_assets.lots.models import Lot

    assert Lot.objects.exists()

    admin_user = get_user_model().objects.create_superuser(username="admin", password="x")
    client.force_login(admin_user)
    for model_name in ("lot", "lotmatch", "lotevent", "washsaleadjustment"):
        response = client.get(reverse(f"admin:django_assets_{model_name}_changelist"))
        assert response.status_code == 200
        # read-mostly: no add button for derived rows
        assert f"/{model_name}/add/" not in response.content.decode()
    response = client.get(reverse("admin:django_assets_exerciselink_changelist"))
    assert response.status_code == 200  # links ARE editable


def test_drf_read_only_no_auth(accounts, aapl, buy):
    from rest_framework.test import APIRequestFactory

    from django_assets import viewsets

    buy("100", "50.00", at(0))
    from django_assets.lots.rebuild import rebuild_lots

    rebuild_lots(accounts["holdings"])
    factory = APIRequestFactory()
    response = viewsets.LotViewSet.as_view({"get": "list"})(factory.get("/lots/"))
    assert response.status_code == 200
    assert len(response.data) == 1
    for name in ("LotViewSet", "LotMatchViewSet"):
        cls = getattr(viewsets, name)
        assert "authentication_classes" not in vars(cls)
        assert "permission_classes" not in vars(cls)


def test_owner_scoping_when_host_mounts_auth(accounts, aapl, buy, user):
    """D-18 refined: no default auth ships, but an authenticated request
    only ever sees its own books."""
    from rest_framework.test import APIRequestFactory, force_authenticate

    from django_assets import viewsets
    from django_assets.lots.rebuild import rebuild_lots

    buy("100", "50.00", at(0))
    rebuild_lots(accounts["holdings"])

    stranger = get_user_model().objects.create_user(username="stranger", password="x")
    factory = APIRequestFactory()
    request = factory.get("/lots/")
    force_authenticate(request, user=stranger)
    response = viewsets.LotViewSet.as_view({"get": "list"})(request)
    assert response.data == []  # nothing of the other user leaks

    request = factory.get("/lots/")
    force_authenticate(request, user=user)
    response = viewsets.LotViewSet.as_view({"get": "list"})(request)
    assert len(response.data) == 1
