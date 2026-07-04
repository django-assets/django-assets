"""Account single owner + CASCADE on user hard-delete (ADR-0005/0006).

GDPR Article 17 is one User.delete() call; shared reference data is never
touched by it.
"""

import pytest
from django.contrib.auth import get_user_model

from django_assets.core.models import Account, Exchange, Instrument

pytestmark = pytest.mark.django_db


def test_user_delete_cascades_accounts_but_not_reference_data():
    user = get_user_model().objects.create_user(username="gdpr", password="x")
    Account.objects.create(owner=user, name="brokerage cash")
    Account.objects.create(owner=user, name="brokerage holdings")
    Exchange.objects.create(code="XNAS", name="Nasdaq", timezone="America/New_York")
    Instrument.objects.create(code="AAPL")

    user.delete()

    assert Account.objects.count() == 0
    assert Exchange.objects.count() == 1
    assert Instrument.objects.count() == 1


def test_account_requires_owner():
    user = get_user_model().objects.create_user(username="u", password="x")
    account = Account.objects.create(owner=user, name="cash")
    assert account.owner == user
    assert list(user.accounts.all()) == [account]
