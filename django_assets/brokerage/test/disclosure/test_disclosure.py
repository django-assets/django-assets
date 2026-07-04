"""B8: disclosure capture (ADR-0023) — the three-phase dividend spine.

Import $100 dividend → ADR advice ($115 gross / $14 ADR fee / $1 tax)
→ 1099 reclassification. The reconciled leg stays byte-identical
throughout; every prior state is one snapshot away.
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from django_assets.brokerage.disclosure import (
    DisclosureEdits,
    LegEdit,
    NewLeg,
    apply_disclosure,
    reconstruct_before,
    reconstruct_original,
    snapshot_transaction,
)
from django_assets.brokerage.exceptions import ReconciledLegLocked
from django_assets.brokerage.models import DisclosureEvent, ImportBatch, ImportLine
from django_assets.brokerage.test.imports.conftest import TS
from django_assets.instruments.equities import templates

pytestmark = pytest.mark.ledger

D = Decimal


@pytest.fixture
def imported_dividend(accounts, usd, aapl):
    """Phase 1: the broker CSV said 'dividend $100'; the cash leg is
    reconciled (locked)."""
    tx = templates.dividend_received(
        accounts=accounts, instrument=aapl, amount="100.00", timestamp=TS, origin="import"
    )
    batch = ImportBatch.objects.create(
        account=accounts["cash"],
        schema_broker="schwab",
        schema_document_kind="trades",
        schema_format_kind="csv",
        schema_version="2026.1",
    )
    line = ImportLine.objects.create(batch=batch, line_number=1, kind="broker_dividend")
    line.matched_legs.add(tx.legs.get(account=accounts["cash"]))
    return tx


def adr_advice_edits(tx, account_map):
    """$115 gross, $14 ADR fee, $1 tax — net $100 unchanged."""
    external_leg = tx.legs.get(account=account_map["external"])
    usd_instrument = external_leg.instrument
    return DisclosureEdits(
        revised=[LegEdit(leg=external_leg, amount="-115.00")],
        added=[
            NewLeg(account=account_map["adr_fees"], instrument=usd_instrument, amount="14.00"),
            NewLeg(account=account_map["tax_withheld"], instrument=usd_instrument, amount="1.00"),
        ],
    )


def test_three_phase_dividend(imported_dividend, accounts, usd):
    tx = imported_dividend
    cash_leg = tx.legs.get(account=accounts["cash"])
    as_imported = snapshot_transaction(tx)

    # Phase 2: the ADR advice arrives.
    event1 = apply_disclosure(
        tx,
        source="adr_advice",
        reference="adr.com advice 2026-03-15.pdf",
        edits=adr_advice_edits(tx, accounts),
    )
    tx.refresh_from_db()
    assert tx.legs.count() == 4
    assert tx.legs.get(account=accounts["adr_fees"]).amount == D("14.00")
    # (a) The reconciled leg is byte-identical.
    cash_leg.refresh_from_db()
    assert cash_leg.amount == D("100.00")
    # (c) The first snapshot IS the as-imported materialization.
    assert event1.snapshot_before == as_imported

    # Phase 3: the 1099 reclassifies (annotation-level edit).
    phase2_state = snapshot_transaction(tx)
    tax_leg = tx.legs.get(account=accounts["tax_withheld"])
    event2 = apply_disclosure(
        tx,
        source="1099_div",
        reference="1099-DIV 2026",
        edits=DisclosureEdits(
            revised=[LegEdit(leg=tax_leg, description="nonresident withholding, box 7")]
        ),
    )
    # (b) Each phase's snapshot reconstructs the prior state exactly.
    assert event2.snapshot_before == phase2_state
    assert reconstruct_before(event2) == phase2_state
    assert reconstruct_original(tx) == as_imported
    cash_leg.refresh_from_db()
    assert cash_leg.amount == D("100.00")
    assert tx.disclosure_events.count() == 2


def test_locked_leg_in_edits_raises(imported_dividend, accounts):
    tx = imported_dividend
    cash_leg = tx.legs.get(account=accounts["cash"])
    with pytest.raises(ReconciledLegLocked):
        apply_disclosure(
            tx,
            source="adr_advice",
            edits=DisclosureEdits(revised=[LegEdit(leg=cash_leg, amount="115.00")]),
        )
    assert DisclosureEvent.objects.count() == 0
    cash_leg.refresh_from_db()
    assert cash_leg.amount == D("100.00")


def test_unbalanced_edits_rejected_at_commit(imported_dividend, accounts):
    from django.db import IntegrityError
    from django.db import transaction as db_tx

    tx = imported_dividend
    external_leg = tx.legs.get(account=accounts["external"])
    with pytest.raises(IntegrityError), db_tx.atomic():
        apply_disclosure(
            tx,
            source="adr_advice",
            edits=DisclosureEdits(revised=[LegEdit(leg=external_leg, amount="-115.00")]),
        )


def test_helper_works_on_manual_transactions(accounts, usd, aapl):
    """Never-imported manuals get the same audit trail."""
    tx = templates.dividend_received(
        accounts=accounts, instrument=aapl, amount="50.00", timestamp=TS
    )
    external_leg = tx.legs.get(account=accounts["external"])
    event = apply_disclosure(
        tx,
        source="manual_correction",
        note="was actually 55 gross w/ 5 fee",
        edits=DisclosureEdits(
            revised=[LegEdit(leg=external_leg, amount="-55.00")],
            added=[
                NewLeg(
                    account=accounts["adr_fees"],
                    instrument=external_leg.instrument,
                    amount="5.00",
                )
            ],
        ),
    )
    assert event.transaction == tx
    assert tx.legs.count() == 3


def test_removed_leg_ids(imported_dividend, accounts):
    tx = imported_dividend
    apply_disclosure(
        tx,
        source="adr_advice",
        edits=adr_advice_edits(tx, accounts),
    )
    fee_leg = tx.legs.get(account=accounts["adr_fees"])
    tax_leg = tx.legs.get(account=accounts["tax_withheld"])
    external_leg = tx.legs.get(account=accounts["external"])
    # Removals pair with a revision so the trigger stays satisfied.
    apply_disclosure(
        tx,
        source="broker_reconciliation",
        edits=DisclosureEdits(
            revised=[LegEdit(leg=external_leg, amount="-100.00")],
            removed=[fee_leg.pk, tax_leg.pk],
        ),
    )
    tx.refresh_from_db()
    assert tx.legs.count() == 2


def test_reconstruction_surfaces(imported_dividend, accounts, usd, client):
    tx = imported_dividend
    as_imported = snapshot_transaction(tx)
    apply_disclosure(tx, source="adr_advice", edits=adr_advice_edits(tx, accounts))
    event = tx.disclosure_events.get()

    # DRF endpoints return TransactionSerializer-shaped payloads that
    # match the pre-edit state field-for-field.
    from rest_framework.test import APIRequestFactory

    from django_assets.viewsets import DisclosureEventViewSet, TransactionViewSet

    factory = APIRequestFactory()
    response = TransactionViewSet.as_view({"get": "original"})(
        factory.get(f"/transactions/{tx.pk}/original/"), pk=tx.pk
    )
    assert response.status_code == 200
    assert response.data == as_imported
    assert {"account", "timestamp", "description", "origin", "legs"} <= set(response.data)

    response = DisclosureEventViewSet.as_view({"get": "before"})(
        factory.get(f"/disclosure-events/{event.pk}/before/"), pk=event.pk
    )
    assert response.status_code == 200
    assert response.data == as_imported

    # Admin pages render structured records, never raw JSON.
    admin_user = get_user_model().objects.create_superuser(username="admin", password="x")
    client.force_login(admin_user)
    page = client.get(reverse("admin:django_assets_disclosure_before", args=[event.pk]))
    assert page.status_code == 200
    body = page.content.decode()
    assert "-100.00" in body and "<table" in body
    assert "snapshot_before" not in body  # not a JSON dump

    page = client.get(reverse("admin:django_assets_transaction_original", args=[tx.pk]))
    assert page.status_code == 200
    assert "-100.00" in page.content.decode()
