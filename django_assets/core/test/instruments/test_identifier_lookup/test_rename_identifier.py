"""Instrument.rename_identifier — the ADR-0009 operational-hygiene helper.

FISV -> FI: deactivate the old ticker (setting effective_to) and create the
new one (effective_from) atomically, on the same Instrument.
"""

import datetime

import pytest

from django_assets.core.models import Exchange, Identifier, Instrument

pytestmark = pytest.mark.django_db


def test_rename_identifier_atomic_swap():
    exchange = Exchange.objects.create(code="XNYS", name="NYSE", timezone="America/New_York")
    fiserv = Instrument.objects.create(code="FISV")
    Identifier.objects.create(
        instrument=fiserv,
        type="ticker",
        value="FISV",
        exchange=exchange,
        effective_from=datetime.date(2019, 1, 1),
    )
    cutover = datetime.date(2024, 6, 10)

    new = fiserv.rename_identifier("FISV", "FI", on=cutover)

    old = Identifier.objects.get(value="FISV")
    assert old.is_active is False
    assert old.effective_to == cutover
    assert new.instrument == fiserv
    assert new.value == "FI"
    assert new.is_active is True
    assert new.effective_from == cutover
    assert new.exchange == exchange
    assert new.type == "ticker"


def test_rename_missing_identifier_raises():
    inst = Instrument.objects.create(code="X")
    with pytest.raises(Identifier.DoesNotExist):
        inst.rename_identifier("NOPE", "NEW", on=datetime.date(2026, 1, 1))
