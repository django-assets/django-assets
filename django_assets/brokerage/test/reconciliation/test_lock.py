"""B6: the reconciled-leg lock (spec §6, ADR-0024, D-17).

A leg is reconciled iff some ImportLine.matched_legs references it.
Numeric facts (amount/account/instrument) are broker ground truth —
locked; description/metadata stay editable. Unflip → edit → re-match
is the deliberate workflow.
"""

from decimal import Decimal

import pytest

from django_assets.brokerage.exceptions import ReconciledLegLocked
from django_assets.brokerage.models import AccountProfile
from django_assets.core.models import Account, TransactionLeg

pytestmark = pytest.mark.ledger

D = Decimal


def _matched_leg(processed) -> TransactionLeg:
    line = processed.lines.filter(kind="broker_trade").first()
    return line.matched_legs.select_related("transaction", "account").first()


def test_amount_edit_locked(processed):
    leg = _matched_leg(processed)
    leg.amount = leg.amount + 1
    with pytest.raises(ReconciledLegLocked):
        leg.save()


def test_account_edit_locked(processed, user):
    leg = _matched_leg(processed)
    leg.account = Account.objects.create(owner=user, name="elsewhere")
    with pytest.raises(ReconciledLegLocked):
        leg.save()


def test_instrument_edit_locked(processed, usd):
    leg = _matched_leg(processed)
    leg.instrument = usd
    with pytest.raises(ReconciledLegLocked):
        leg.save()


def test_description_and_metadata_edits_pass(processed):
    """D-17: only the numeric facts are broker ground truth."""
    leg = _matched_leg(processed)
    leg.description = "annotated by user"
    leg.metadata = {"note": "checked"}
    leg.save()
    leg.refresh_from_db()
    assert leg.description == "annotated by user"


def test_delete_leg_blocked(processed):
    leg = _matched_leg(processed)
    with pytest.raises(ReconciledLegLocked):
        leg.delete()


def test_delete_parent_transaction_blocked(processed):
    leg = _matched_leg(processed)
    with pytest.raises(ReconciledLegLocked):
        leg.transaction.delete()


def test_unflip_edit_rematch_round_trip(processed):
    from django.db import transaction as db_tx

    line = processed.lines.filter(kind="broker_trade").first()
    leg = line.matched_legs.first()
    counterpart = leg.transaction.legs.filter(instrument=leg.instrument).exclude(pk=leg.pk).first()
    original, counter_original = leg.amount, counterpart.amount

    line.matched_legs.remove(leg)  # unflip: back to the unmatched pool
    with db_tx.atomic():  # paired edit keeps the trigger satisfied
        leg.amount = original + 1
        leg.save()
        counterpart.amount = counter_original - 1
        counterpart.save()
    with db_tx.atomic():  # and revert
        leg.amount = original
        leg.save()
        counterpart.amount = counter_original
        counterpart.save()
    line.matched_legs.add(leg)  # re-match is normal M2M add
    leg.refresh_from_db()
    with pytest.raises(ReconciledLegLocked):
        leg.amount = original + 1
        leg.save()  # pre_save raises before any write


def test_unreconciled_legs_stay_editable(processed, accounts):
    """Fee/counterparty legs from the same rows are never locked."""
    leg = TransactionLeg.objects.filter(account=accounts["external"]).first()
    leg.amount = leg.amount  # no-op numeric write
    leg.save()  # no error: not in any matched_legs


def test_unflip_guard_end_to_end(processed, accounts):
    """Completes the B1 stub: clearing allows_reconciliation while
    matched legs reference the account raises; after unflipping all of
    them, clearing succeeds."""
    profile = AccountProfile.objects.get(account=accounts["holdings"])
    profile.allows_reconciliation = False
    with pytest.raises(ValueError, match="unmatch"):
        profile.save()

    for line in processed.lines.all():
        line.matched_legs.set(line.matched_legs.exclude(account=accounts["holdings"]))
    profile.allows_reconciliation = False
    profile.save()
    profile.refresh_from_db()
    assert profile.allows_reconciliation is False


def test_origin_never_changes_on_match_or_unflip(processed):
    """ADR-0028: provenance is orthogonal to reconciliation."""
    line = processed.lines.filter(kind="broker_trade").first()
    leg = line.matched_legs.first()
    assert leg.transaction.origin == "import"
    line.matched_legs.remove(leg)
    leg.transaction.refresh_from_db()
    assert leg.transaction.origin == "import"
