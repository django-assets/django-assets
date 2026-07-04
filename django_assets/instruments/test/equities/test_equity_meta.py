"""I2: EquityMeta (D-11, minimal v0.x)."""

import pytest
from django.db import IntegrityError

from django_assets.core.models import Exchange, Instrument
from django_assets.instruments.equities.models import EquityMeta

pytestmark = pytest.mark.django_db


def test_equity_meta_one_to_one():
    nyse = Exchange.objects.create(code="XNYS", name="NYSE", timezone="America/New_York")
    aapl = Instrument.objects.create(code="AAPL", quantity_decimals=0)
    EquityMeta.objects.create(instrument=aapl, primary_exchange=nyse)
    assert aapl.equity_meta.primary_exchange == nyse
    with pytest.raises(IntegrityError):
        EquityMeta.objects.create(instrument=aapl)


def test_primary_exchange_nullable_and_categorization():
    inst = Instrument.objects.create(code="PRIV", quantity_decimals=0)
    EquityMeta.objects.create(instrument=inst)
    assert inst.equity_meta.primary_exchange is None
    assert list(Instrument.objects.filter(equity_meta__isnull=False)) == [inst]
