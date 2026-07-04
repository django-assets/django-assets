"""C7: admin surface smoke tests — spec §8, ADR-0017/0022.

Fully editable admin; the trigger (plus a friendly inline-formset
pre-check with the same rule) is the integrity gate. The reversal
pattern is documented, not enforced.
"""

import datetime
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from django_assets.core.builder import TransactionBuilder
from django_assets.core.models import (
    Account,
    Exchange,
    Identifier,
    Instrument,
    Transaction,
    TransactionLeg,
)

pytestmark = pytest.mark.django_db

D = Decimal
TS = datetime.datetime(2026, 3, 13, 20, 0, tzinfo=datetime.UTC)


@pytest.fixture
def admin_user():
    return get_user_model().objects.create_superuser(username="admin", password="x")


@pytest.fixture
def client(admin_user, client):
    client.force_login(admin_user)
    return client


@pytest.fixture
def fixture_set(admin_user):
    usd = Instrument.objects.create(code="USD", quantity_decimals=2)
    nyse = Exchange.objects.create(code="XNYS", name="NYSE", timezone="America/New_York")
    aapl = Instrument.objects.create(code="AAPL", quantity_decimals=0, price_currency=usd)
    Identifier.objects.create(instrument=aapl, type="ticker", value="AAPL", exchange=nyse)
    cash = Account.objects.create(owner=admin_user, name="cash")
    external = Account.objects.create(owner=admin_user, name="external")
    with TransactionBuilder(account=cash, timestamp=TS, description="deposit") as b:
        b.add_leg(account=cash, instrument=usd, amount="100.00")
        b.add_leg(account=external, instrument=usd, amount="-100.00")
    return {
        "usd": usd,
        "aapl": aapl,
        "cash": cash,
        "external": external,
        "tx": b.transaction,
    }


@pytest.mark.parametrize(
    "model_name",
    ["exchange", "instrument", "identifier", "account", "transaction", "transactionleg"],
)
def test_changelists_render(client, fixture_set, model_name):
    url = reverse(f"admin:django_assets_{model_name}_changelist")
    assert client.get(url).status_code == 200


def test_transaction_change_form_has_leg_inline(client, fixture_set):
    url = reverse("admin:django_assets_transaction_change", args=[fixture_set["tx"].pk])
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert "legs-TOTAL_FORMS" in body  # TransactionLegInline formset


def test_origin_read_only_on_change_but_editable_on_add(client, fixture_set):
    change = client.get(
        reverse("admin:django_assets_transaction_change", args=[fixture_set["tx"].pk])
    ).content.decode()
    add = client.get(reverse("admin:django_assets_transaction_add")).content.decode()
    assert '<input type="text" name="origin"' not in change  # rendered read-only
    assert 'name="origin"' in add


def _change_form_data(tx, legs, amounts):
    """POST payload for the transaction change form with edited leg amounts."""
    data = {
        "account": str(tx.account_id),
        "timestamp_0": "2026-03-13",
        "timestamp_1": "20:00:00",
        "trade_timestamp_0": "",
        "trade_timestamp_1": "",
        "description": tx.description,
        "metadata": "{}",
        "legs-TOTAL_FORMS": str(len(legs)),
        "legs-INITIAL_FORMS": str(len(legs)),
        "legs-MIN_NUM_FORMS": "0",
        "legs-MAX_NUM_FORMS": "1000",
        "_save": "Save",
    }
    for i, (leg, amount) in enumerate(zip(legs, amounts, strict=True)):
        data[f"legs-{i}-id"] = str(leg.pk)
        data[f"legs-{i}-transaction"] = str(tx.pk)
        data[f"legs-{i}-account"] = str(leg.account_id)
        data[f"legs-{i}-instrument"] = str(leg.instrument_id)
        data[f"legs-{i}-amount"] = amount
        data[f"legs-{i}-description"] = ""
        data[f"legs-{i}-metadata"] = "{}"
    return data


def test_unbalancing_leg_edit_is_rejected_with_clear_error(client, fixture_set):
    tx = fixture_set["tx"]
    legs = list(tx.legs.order_by("id"))
    url = reverse("admin:django_assets_transaction_change", args=[tx.pk])
    response = client.post(url, _change_form_data(tx, legs, ["100.00", "-55.00"]))
    assert response.status_code == 200  # re-rendered with errors, not saved
    assert "balanced" in response.content.decode()
    legs[1].refresh_from_db()
    assert legs[1].amount == D("-100.00")


def test_balanced_leg_edit_saves(client, fixture_set):
    tx = fixture_set["tx"]
    legs = list(tx.legs.order_by("id"))
    url = reverse("admin:django_assets_transaction_change", args=[tx.pk])
    response = client.post(url, _change_form_data(tx, legs, ["55.00", "-55.00"]))
    assert response.status_code == 302  # saved, redirected
    legs[0].refresh_from_db()
    assert legs[0].amount == D("55.00")
    assert Transaction.objects.count() == 1
    assert TransactionLeg.objects.count() == 2


def test_instrument_search_by_identifier_value(client, fixture_set):
    """spec §8: InstrumentAdmin searches by code AND identifier value."""
    url = reverse("admin:django_assets_instrument_changelist")
    response = client.get(url, {"q": "AAPL"})
    assert response.status_code == 200
    assert "AAPL" in response.content.decode()
