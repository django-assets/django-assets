"""I3: OptionMeta + Deliverable schema — ADR-0010 verbatim."""

import datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError

from django_assets.instruments.options.models import Deliverable, OptionMeta

from .conftest import CUTOVER, LISTED

pytestmark = pytest.mark.django_db

D = Decimal


def test_option_meta_one_to_one_and_underlying(pfe1_call, pfe):
    meta = pfe1_call.option_meta
    assert meta.underlying == pfe
    assert meta.strike == D("35")
    assert pfe.option_meta_as_underlying.count() == 1
    with pytest.raises(IntegrityError):
        OptionMeta.objects.create(
            instrument=pfe1_call, underlying=pfe, expiry=meta.expiry, strike=D("1"), right="C"
        )


def test_deliverable_check_instrument_xor_cash(pfe1_call, pfe, usd):
    meta = pfe1_call.option_meta
    # Both sides set → CHECK violation.
    with pytest.raises(IntegrityError):
        Deliverable.objects.create(
            option_meta=meta,
            instrument=pfe,
            quantity=D("100"),
            cash_currency=usd,
            cash_amount=D("1"),
            effective_from=LISTED,
        )


def test_deliverable_check_neither_side(pfe1_call):
    with pytest.raises(IntegrityError):
        Deliverable.objects.create(option_meta=pfe1_call.option_meta, effective_from=LISTED)


def test_active_at_half_open_boundaries(pfe1_call):
    """[effective_from, effective_to): from-date active, to-date not."""
    meta = pfe1_call.option_meta
    day_before = CUTOVER - datetime.timedelta(days=1)
    assert [d.sequence for d in meta.active_deliverables(day_before)] == [0]
    active_on_cutover = meta.active_deliverables(CUTOVER)
    assert len(active_on_cutover) == 3  # the adjusted basket starts ON the date
    assert all(d.corporate_action is not None for d in active_on_cutover)
    # The pre-spinoff row died AT its effective_to.
    assert all(d.effective_from == CUTOVER for d in active_on_cutover)
    # Before listing: nothing.
    assert meta.active_deliverables(LISTED - datetime.timedelta(days=1)) == []
    # At listing: the original row starts.
    assert [d.quantity for d in meta.active_deliverables(LISTED)] == [D("100")]


def test_categorization_by_presence(pfe1_call, pfe):
    from django_assets.core.models import Instrument

    options = Instrument.objects.filter(option_meta__isnull=False)
    assert list(options) == [pfe1_call]
