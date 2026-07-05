"""Corporate-action detection (ADR-0036): statement position
checkpoints, residual classification, and the approve/reject loop.

The pipeline per checkpoint:

1. ``ledger_positions`` — the holdings account's per-instrument
   quantities at the statement boundary, evaluated on TRADE timestamps
   (holdings tables are trade-date-basis; settle-date evaluation would
   false-positive on month-end straddles).
2. Explanation — each statement position resolves to a ledger
   instrument by ANY known identifier (option code, CUSIP, ticker) and
   consumes its quantity. Never creates instruments.
3. Residual classification — conservation heuristics turn the
   leftovers into rename / split / merger proposals; everything else
   is ``unexplained``. Proposals are fingerprint-stable: re-running
   detection never duplicates or re-raises a resolved proposal.
4. Review — ``approve_proposal`` books the mapped template
   (convert_instrument / stock_split) with the proposal in metadata;
   ``reject_proposal`` records the dismissal. Nothing ever books
   automatically.
"""

import datetime
import hashlib
from decimal import Decimal
from typing import Any

from django.db.models import Q, Sum
from django.utils import timezone

from django_assets.brokerage.models import CorporateActionProposal
from django_assets.core.models import Account, Identifier, Instrument, TransactionLeg

#: split/reverse ratios considered "simple" enough to propose
SPLIT_RATIOS = [Decimal(n) for n in (2, 3, 4, 5, 6, 7, 8, 10, 15, 20)]


def ledger_positions(account: Account, as_of: datetime.date) -> "dict[Instrument, Decimal]":
    """Non-currency positions at end-of-day `as_of`, on trade dates."""
    boundary = datetime.datetime.combine(as_of, datetime.time(23, 59, 59), tzinfo=datetime.UTC)
    rows = (
        TransactionLeg.objects.filter(account=account)
        .filter(
            Q(transaction__trade_timestamp__lte=boundary)
            | Q(transaction__trade_timestamp__isnull=True, transaction__timestamp__lte=boundary)
        )
        .values("instrument")
        .annotate(total=Sum("amount"))
    )
    instruments = Instrument.objects.in_bulk([r["instrument"] for r in rows])
    return {instruments[r["instrument"]]: r["total"] for r in rows if r["total"] != 0}


def _resolve(position: Any) -> "Instrument | None":
    """Match a statement position to an existing instrument by any of
    its identities — resolve-only, never create (ADR-0036 §2)."""
    for id_type, value in position.identities():
        identifier = (
            Identifier.objects.filter(type=id_type, value=value, is_active=True)
            .select_related("instrument")
            .first()
        )
        if identifier is not None:
            return identifier.instrument
    return None


def _fingerprint(account: Account, kind: str, parts: "list[str]") -> str:
    """Deliberately date-free: a persistent mismatch (an expired option
    the importer never closed, an unapproved rename) is ONE finding,
    not one per month it remains unresolved. statement_date records
    first sighting."""
    raw = "|".join([str(account.pk), kind, *parts])
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def run_checkpoint(
    *,
    account: Account,
    positions: "list[Any]",
    as_of: datetime.date,
    source_reference: str = "",
) -> "list[CorporateActionProposal]":
    """Diff one statement's closing holdings against the ledger and
    persist proposals for the residuals. Idempotent per fingerprint."""
    ledger = ledger_positions(account, as_of)
    if not positions and ledger:
        # Nothing parsed but the ledger holds positions: either an
        # inactive-format variant without a holdings table or a parser
        # gap — not an assertable checkpoint. Silence beats 3,000
        # phantom "unexplained" rows.
        return []
    ledger_left = dict(ledger)
    stmt_left: list[Any] = []

    for position in positions:
        instrument = _resolve(position)
        if instrument is not None and instrument in ledger_left:
            if ledger_left[instrument] == position.quantity:
                del ledger_left[instrument]  # explained
                continue
            # same identity, different quantity → split candidate
            stmt_left.append((position, instrument))
            continue
        stmt_left.append((position, instrument))

    proposals: list[CorporateActionProposal] = []

    def propose(kind: str, **kwargs: Any) -> None:
        parts = [
            str(kwargs.get("from_instrument") and kwargs["from_instrument"].pk),
            str(kwargs.get("to_instrument") and kwargs["to_instrument"].pk),
            str(kwargs.get("from_quantity")),
            str(kwargs.get("to_quantity")),
            kwargs.get("evidence", {}).get("statement_label", ""),
        ]
        fingerprint = _fingerprint(account, kind, parts)
        proposal, created = CorporateActionProposal.objects.get_or_create(
            account=account,
            fingerprint=fingerprint,
            defaults={
                "statement_date": as_of,
                "source_reference": source_reference,
                "action_kind": kind,
                **kwargs,
            },
        )
        if created:
            proposals.append(proposal)

    # -- same-instrument quantity drift: split / reverse split ---------
    unpaired_stmt: list[Any] = []
    for position, instrument in stmt_left:
        if instrument is None or instrument not in ledger_left:
            unpaired_stmt.append((position, instrument))
            continue
        held = ledger_left[instrument]
        ratio = _simple_ratio(held, position.quantity)
        kind = "split" if ratio is not None else "unexplained"
        propose(
            kind,
            from_instrument=instrument,
            to_instrument=instrument,
            from_quantity=held,
            to_quantity=position.quantity,
            evidence={
                "statement_label": position.label(),
                "statement_quantity": str(position.quantity),
                "ledger_quantity": str(held),
                **({"ratio": str(ratio)} if ratio is not None else {}),
            },
        )
        del ledger_left[instrument]

    # -- disappear/appear pairs: rename (qty conserved) or merger ------
    ledger_only = [(inst, qty) for inst, qty in ledger_left.items() if not _is_currency(inst)]
    stmt_only = [(p, inst) for p, inst in unpaired_stmt if inst is None or inst not in ledger]

    used_stmt: set[int] = set()
    for inst, qty in list(ledger_only):
        exact = [
            i
            for i, (p, _t) in enumerate(stmt_only)
            if i not in used_stmt and p.quantity == qty and _rename_compatible(inst, p)
        ]
        if len(exact) == 1:
            p, target = stmt_only[exact[0]]
            used_stmt.add(exact[0])
            propose(
                "rename",
                from_instrument=inst,
                to_instrument=target,
                from_quantity=qty,
                to_quantity=p.quantity,
                evidence={
                    "statement_label": p.label(),
                    "statement_identities": dict(p.identities()),
                    "statement_description": p.description,
                    "ledger_code": inst.code,
                    "quantity_conserved": True,
                },
            )
            ledger_only.remove((inst, qty))

    if len(ledger_only) == 1 and len(stmt_only) - len(used_stmt) == 1:
        inst, qty = ledger_only[0]
        p, target = next(entry for i, entry in enumerate(stmt_only) if i not in used_stmt)
        propose(
            "merger",
            from_instrument=inst,
            to_instrument=target,
            from_quantity=qty,
            to_quantity=p.quantity,
            evidence={
                "statement_label": p.label(),
                "statement_identities": dict(p.identities()),
                "ledger_code": inst.code,
            },
        )
    else:
        for inst, qty in ledger_only:
            propose(
                "unexplained",
                from_instrument=inst,
                to_instrument=None,
                from_quantity=qty,
                to_quantity=None,
                evidence={"direction": "ledger_only", "ledger_code": inst.code},
            )
        for i, (p, target) in enumerate(stmt_only):
            if i in used_stmt:
                continue
            propose(
                "unexplained",
                from_instrument=None,
                to_instrument=target,
                from_quantity=None,
                to_quantity=p.quantity,
                evidence={
                    "direction": "statement_only",
                    "statement_label": p.label(),
                    "statement_identities": dict(p.identities()),
                },
            )
    return proposals


def _rename_compatible(instrument: Instrument, position: Any) -> bool:
    """Option contracts never rename strike-to-strike — only the
    UNDERLYING's symbol can change, which preserves expiry/strike/right
    (FB→META). Pairing arbitrary equal-quantity option legs would
    misread a rolled position as a rename."""
    ledger_option = _option_tail(instrument.code)
    stmt_option = _option_tail(position.option_code) if position.option_code else None
    if ledger_option is None and stmt_option is None:
        return True  # equity↔equity: quantity conservation decides
    if ledger_option is None or stmt_option is None:
        return False  # option↔equity is never a rename
    return ledger_option == stmt_option  # expiry+strike+right conserved


def _option_tail(code: str) -> "tuple[str, str, str] | None":
    parts = code.split()
    if len(parts) == 4 and parts[3] in ("C", "P") and "/" in parts[1]:
        return (parts[1], parts[2], parts[3])
    return None


def _is_currency(instrument: Instrument) -> bool:
    """Currencies live in cash accounts; a holdings checkpoint compares
    securities only, but guard anyway (multi-currency cash never enters
    holdings in shipped templates)."""
    return instrument.price_currency_id is None and instrument.multiplier == 1


def _simple_ratio(old: Decimal, new: Decimal) -> "Decimal | None":
    if not old or not new or old.copy_sign(1) != old or new.copy_sign(1) != new:
        return None
    for ratio in SPLIT_RATIOS:
        if new == old * ratio:
            return ratio
        if old == new * ratio:
            return Decimal(1) / ratio
    return None


def approve_proposal(
    proposal: CorporateActionProposal,
    *,
    to_instrument: "Instrument | None" = None,
    note: str = "",
) -> Any:
    """Book the user-approved interpretation. `to_instrument` supplies
    the target when detection couldn't resolve one (a renamed ticker
    that never traded before has no instrument yet — the caller
    creates/chooses it deliberately; detection never does)."""
    from django_assets.brokerage.accounts import ensure_standard_accounts
    from django_assets.instruments.equities import templates as eq

    if proposal.resolution:
        raise ValueError(f"proposal already {proposal.resolution}")
    target = to_instrument or proposal.to_instrument
    accounts = ensure_standard_accounts(proposal.account.owner)
    accounts["holdings"] = proposal.account
    timestamp = datetime.datetime.combine(
        proposal.statement_date, datetime.time(21, 0), tzinfo=datetime.UTC
    )

    if proposal.action_kind in ("rename", "merger"):
        if proposal.from_instrument is None or target is None:
            raise ValueError("rename/merger approval needs both instruments")
        if proposal.from_quantity is None or proposal.to_quantity is None:
            raise ValueError("rename/merger approval needs both quantities")
        transaction = eq.convert_instrument(
            accounts=accounts,
            from_instrument=proposal.from_instrument,
            to_instrument=target,
            from_quantity=proposal.from_quantity,
            to_quantity=proposal.to_quantity,
            timestamp=timestamp,
        )
    elif proposal.action_kind == "split":
        if (
            proposal.from_instrument is None
            or proposal.from_quantity is None
            or proposal.to_quantity is None
        ):
            raise ValueError("split approval needs the instrument and quantities")
        additional = proposal.to_quantity - proposal.from_quantity
        ratio = Decimal(proposal.evidence.get("ratio", "0")) or (
            proposal.to_quantity / proposal.from_quantity
        )
        transaction = eq.stock_split(
            accounts=accounts,
            instrument=proposal.from_instrument,
            additional_quantity=additional,
            ratio=ratio,
            timestamp=timestamp,
        )
    else:
        raise ValueError(
            f"{proposal.action_kind!r} proposals have no template mapping — "
            "investigate and book manually, then reject with a note"
        )

    # The evidence chain: FK on the proposal plus a metadata pointer on
    # the booked transaction (its own template already wrote the
    # ADR-0032 conversion/corporate-action tag).
    transaction.metadata = {
        **transaction.metadata,
        "corporate_action_proposal": proposal.pk,
        "source_reference": proposal.source_reference,
    }
    transaction.save(update_fields=["metadata"])

    proposal.resolution = "approved"
    proposal.resolved_at = timezone.now()
    proposal.note = note
    proposal.to_instrument = target
    proposal.booked_transaction = transaction
    proposal.save(
        update_fields=["resolution", "resolved_at", "note", "to_instrument", "booked_transaction"]
    )
    return transaction


def reject_proposal(proposal: CorporateActionProposal, *, note: str = "") -> None:
    if proposal.resolution:
        raise ValueError(f"proposal already {proposal.resolution}")
    proposal.resolution = "rejected"
    proposal.resolved_at = timezone.now()
    proposal.note = note
    proposal.save(update_fields=["resolution", "resolved_at", "note"])
