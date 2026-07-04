"""I3: admin + DRF surface smoke (instruments spec §5)."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    user = get_user_model().objects.create_superuser(username="admin", password="x")
    client.force_login(user)
    return client


@pytest.mark.parametrize(
    "model_name",
    ["corporateaction", "currencymeta", "cryptometa", "equitymeta", "optionmeta"],
)
def test_instruments_changelists_render(admin_client, model_name):
    url = reverse(f"admin:django_assets_{model_name}_changelist")
    assert admin_client.get(url).status_code == 200


def test_deliverable_inline_under_option_meta(admin_client, pfe1_call):
    url = reverse(
        "admin:django_assets_optionmeta_change", args=[pfe1_call.option_meta.pk]
    )
    response = admin_client.get(url)
    assert response.status_code == 200
    assert "deliverables-TOTAL_FORMS" in response.content.decode()


def test_read_only_viewsets(pfe1_call, usd):
    """Read-only viewsets, host-mounted, no default auth (ADR-0017)."""
    from rest_framework.test import APIRequestFactory

    from django_assets.viewsets import InstrumentViewSet, OptionMetaViewSet

    factory = APIRequestFactory()
    response = InstrumentViewSet.as_view({"get": "list"})(factory.get("/instruments/"))
    assert response.status_code == 200
    assert len(response.data) >= 2  # the option + USD (+ underlyings)

    response = OptionMetaViewSet.as_view({"get": "list"})(factory.get("/option-metas/"))
    assert response.status_code == 200
    assert response.data[0]["strike"] == "35.00000000"

    # Read-only: no create/update/destroy actions bound.
    assert not hasattr(InstrumentViewSet, "create") or "post" not in [
        m.lower() for m in InstrumentViewSet.http_method_names if m in ("post",)
    ]


def test_serializers_cover_instruments_models(pfe1_call):
    from django_assets.serializers import (
        CorporateActionSerializer,
        DeliverableSerializer,
        OptionMetaSerializer,
    )

    data = OptionMetaSerializer(pfe1_call.option_meta).data
    assert data["right"] == "C"
    assert len(DeliverableSerializer(pfe1_call.option_meta.deliverables.all(), many=True).data) == 4
    assert CorporateActionSerializer is not None
