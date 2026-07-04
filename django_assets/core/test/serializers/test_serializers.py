"""C7: DRF serializers — spec §9, ADR-0017.

Serializers only: no viewsets, no urls, no auth assumptions. Writes go
through TransactionBuilder; decimals render as strings; MeasureField is
{"amount": "...", "unit": "..."}.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.builder import TransactionBuilder
from django_assets.core.measure import Measure
from django_assets.core.models import Account, Instrument, Transaction
from django_assets.serializers import (
    HoldingSerializer,
    MeasureField,
    PortfolioSerializer,
    TransactionSerializer,
)

pytestmark = pytest.mark.django_db

D = Decimal
TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def user():
    return get_user_model().objects.create_user(username="trader", password="x")


@pytest.fixture
def usd():
    return Instrument.objects.create(code="USD", quantity_decimals=2)


@pytest.fixture
def accounts(user):
    return {n: Account.objects.create(owner=user, name=n) for n in ["cash", "external"]}


@pytest.fixture
def tx(accounts, usd):
    with TransactionBuilder(account=accounts["cash"], timestamp=TS, description="dep") as b:
        b.add_leg(account=accounts["cash"], instrument=usd, amount="100.00")
        b.add_leg(account=accounts["external"], instrument=usd, amount="-100.00")
    return b.transaction


def test_transaction_serializes_with_nested_legs(tx, usd):
    data = TransactionSerializer(tx).data
    assert data["description"] == "dep"
    assert data["origin"] == "manual"
    assert len(data["legs"]) == 2
    amounts = sorted(leg["amount"] for leg in data["legs"])
    assert all(isinstance(a, str) for a in amounts)  # decimal-as-string
    assert D(amounts[0]) == D("-100.00")


def test_transaction_write_path_goes_through_builder(accounts, usd):
    payload = {
        "account": accounts["cash"].pk,
        "timestamp": TS.isoformat(),
        "description": "wire",
        "legs": [
            {"account": accounts["cash"].pk, "instrument": usd.pk, "amount": "42.00"},
            {"account": accounts["external"].pk, "instrument": usd.pk, "amount": "-42.00"},
        ],
    }
    serializer = TransactionSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    tx = serializer.save()
    assert isinstance(tx, Transaction)
    assert tx.legs.count() == 2
    assert Transaction.objects.count() == 1


def test_unbalanced_write_is_a_validation_error(accounts, usd):
    payload = {
        "account": accounts["cash"].pk,
        "timestamp": TS.isoformat(),
        "legs": [
            {"account": accounts["cash"].pk, "instrument": usd.pk, "amount": "42.00"},
            {"account": accounts["external"].pk, "instrument": usd.pk, "amount": "-41.00"},
        ],
    }
    serializer = TransactionSerializer(data=payload)
    assert not serializer.is_valid()
    assert "balanced" in str(serializer.errors)
    assert Transaction.objects.count() == 0


def test_measure_field_shape(usd):
    assert MeasureField().to_representation(Measure(D("12.3456"), usd)) == {
        "amount": "12.3456",
        "unit": "USD",
    }


def test_holding_serializer_shape(accounts, usd, tx):
    data = HoldingSerializer(
        {"account": accounts["cash"], "instrument": usd, "quantity": D("100.00")}
    ).data
    assert data == {"account": accounts["cash"].pk, "instrument": "USD", "quantity": "100.00"}


def test_portfolio_serializer_shape(accounts, usd, tx):
    from django_assets.core.queries import Portfolio

    positions = Portfolio.at(accounts["cash"])
    data = PortfolioSerializer({"account": accounts["cash"], "positions": positions}).data
    assert data["account"] == accounts["cash"].pk
    assert data["positions"] == {"USD": "100.00"}
