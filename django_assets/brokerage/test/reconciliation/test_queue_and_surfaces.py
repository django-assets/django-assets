"""B6: unmatched queue + admin/DRF review surfaces (spec §6/§7)."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from django_assets.brokerage.models import ImportLine
from django_assets.brokerage.reconciliation import match_line, unmatch_line, unmatched_lines
from django_assets.core.models import TransactionLeg

pytestmark = pytest.mark.ledger


@pytest.fixture
def unmatched(processed):
    """Unflip one trade line so the queue has content."""
    line = processed.lines.filter(kind="broker_trade").first()
    legs = list(line.matched_legs.all())
    line.matched_legs.clear()
    return line, legs


def test_unmatched_queue_query(processed, unmatched):
    line, _legs = unmatched
    queue = unmatched_lines()
    assert list(queue) == [line]
    # Informational kinds excluded by construction.
    assert not any(entry.kind == "balance_note" for entry in queue)
    # Account scoping.
    assert list(unmatched_lines(account=processed.account)) == [line]


def test_match_and_unmatch_helpers(processed, unmatched, accounts):
    line, legs = unmatched
    match_line(line, legs)
    assert line.matched_legs.count() == len(legs)
    unmatch_line(line, legs[:1])
    assert line.matched_legs.count() == len(legs) - 1


def test_match_refuses_ineligible_legs(processed, unmatched, accounts):
    """D-10: legs on non-reconciling accounts never enter matched_legs."""
    line, _legs = unmatched
    external_leg = TransactionLeg.objects.filter(account=accounts["market"]).first()
    with pytest.raises(ValueError, match="allows_reconciliation"):
        match_line(line, [external_leg])


def test_admin_unmatched_filter_and_lock_indication(processed, unmatched, client):
    admin_user = get_user_model().objects.create_superuser(username="admin", password="x")
    client.force_login(admin_user)

    changelist = reverse("admin:django_assets_importline_changelist")
    response = client.get(changelist, {"matched": "unmatched"})
    assert response.status_code == 200
    body = response.content.decode()
    assert "broker_trade" in body

    # Locked-leg indication on the (re-registered) TransactionLeg admin.
    response = client.get(reverse("admin:django_assets_transactionleg_changelist"))
    assert "reconciled" in response.content.decode()

    # Read-only schema registry page.
    response = client.get(reverse("admin:django_assets_schema_registry"))
    assert response.status_code == 200
    registry_body = response.content.decode()
    assert "schwab" in registry_body
    assert "SchwabTradesCsv2026" in registry_body


def test_drf_queue_and_schema_endpoints(processed, unmatched, accounts):
    from rest_framework.test import APIRequestFactory

    from django_assets.viewsets import ImportLineViewSet, SchemaRegistryViewSet

    line, legs = unmatched
    factory = APIRequestFactory()

    response = SchemaRegistryViewSet.as_view({"get": "list"})(factory.get("/schemas/"))
    assert response.status_code == 200
    assert any(entry["broker"] == "schwab" for entry in response.data)

    response = ImportLineViewSet.as_view({"get": "list"})(
        factory.get("/lines/", {"matched": "unmatched"})
    )
    assert response.status_code == 200
    assert [entry["id"] for entry in response.data] == [line.pk]

    response = ImportLineViewSet.as_view({"post": "match"})(
        factory.post(f"/lines/{line.pk}/match/", {"legs": [leg.pk for leg in legs]}),
        pk=line.pk,
    )
    assert response.status_code == 200
    assert line.matched_legs.count() == len(legs)

    response = ImportLineViewSet.as_view({"post": "unmatch"})(
        factory.post(f"/lines/{line.pk}/unmatch/", {"legs": [legs[0].pk]}),
        pk=line.pk,
    )
    assert response.status_code == 200
    assert line.matched_legs.count() == len(legs) - 1


def test_shipped_viewsets_assume_no_auth(processed):
    """D-18: hosts own auth; shipped viewsets set no auth/permission
    classes of their own."""
    from django_assets import viewsets

    for name in ("ImportLineViewSet", "SchemaRegistryViewSet", "InstrumentViewSet"):
        cls = getattr(viewsets, name)
        assert "authentication_classes" not in vars(cls)
        assert "permission_classes" not in vars(cls)


def test_informational_lines_never_in_queue(processed):
    assert unmatched_lines().filter(kind="balance_note").count() == 0
    assert ImportLine.objects.filter(kind="balance_note").exists()
